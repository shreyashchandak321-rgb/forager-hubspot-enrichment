"""Forager x HubSpot enrichment service.

Endpoints
  GET  /health                      liveness check
  POST /webhooks/company            HubSpot subscription: company.creation
  POST /webhooks/contact            HubSpot subscription: contact.creation
  POST /enrich/company/{hs_id}      manually enrich one HubSpot company
  POST /enrich/contact/{hs_id}      manually enrich one HubSpot contact
  POST /demo/openai                 build the demo set: enrich OpenAI + find 5 people

HubSpot webhook subscriptions are created in your HubSpot app settings
(Settings -> Integrations -> Private Apps / or a public app's webhooks) and
must point at https://<your-railway-domain>/webhooks/company etc.
"""
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from .config import settings
from .enrichment import EnrichmentService

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

app = FastAPI(title="Forager x HubSpot Enrichment")
service = EnrichmentService()


def _check_secret(request: Request) -> None:
    if settings.webhook_secret and request.query_params.get("key") != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad webhook key")


async def _object_ids(request: Request) -> list[str]:
    """HubSpot webhooks POST a JSON list of events; each has an objectId."""
    try:
        events = await request.json()
    except Exception:
        return []
    if isinstance(events, dict):
        events = [events]
    return [str(e["objectId"]) for e in events if e.get("objectId")]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/webhooks/company")
async def company_webhook(request: Request, background: BackgroundTasks) -> dict:
    _check_secret(request)
    ids = await _object_ids(request)
    for company_id in ids:
        background.add_task(service.enrich_company_by_id, company_id)
    return {"received": ids}


@app.post("/webhooks/contact")
async def contact_webhook(request: Request, background: BackgroundTasks) -> dict:
    _check_secret(request)
    ids = await _object_ids(request)
    for contact_id in ids:
        background.add_task(service.enrich_contact_by_id, contact_id)
    return {"received": ids}


@app.post("/enrich/company/{company_id}")
async def enrich_company(company_id: str) -> dict:
    return await service.enrich_company_by_id(company_id)


@app.post("/enrich/contact/{contact_id}")
async def enrich_contact(contact_id: str) -> dict:
    return await service.enrich_contact_by_id(contact_id)


@app.post("/demo/openai")
async def demo_openai() -> dict:
    """Primary deliverable: the OpenAI company fully enriched in HubSpot,
    plus 5 title-filtered people discovered and created under it."""
    company_id = await service.hubspot.find_company_by_domain("openai.com")
    if not company_id:
        company_id = await service.hubspot.create_company(
            {"name": "OpenAI", "domain": "openai.com"})
    result = await service.enrich_company_by_id(company_id, min_contacts=5)
    return {"company_id": company_id, "result": result}
