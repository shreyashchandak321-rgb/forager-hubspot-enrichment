"""HubSpot CRM API v3 client (Private App / service key access token)."""
import logging

import httpx

from .config import settings

log = logging.getLogger("hubspot")

BASE = "https://api.hubapi.com"

# Properties we read/write. Create any missing custom properties in HubSpot
# (Settings -> Properties) or trim this list to your portal.
COMPANY_PROPERTIES = [
    "name", "domain", "description", "annualrevenue", "numberofemployees",
    "industry", "city", "state", "country", "linkedin_company_page",
]
CONTACT_PROPERTIES = [
    "firstname", "lastname", "email", "phone", "jobtitle",
    "hs_linkedin_url", "company", "website", "city", "state", "country",
]


class HubSpotClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE,
            headers={
                "Authorization": f"Bearer {settings.hubspot_access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    @staticmethod
    def _raise(resp: httpx.Response) -> None:
        if resp.is_error:  # HubSpot's body names the offending property — log it
            log.error("HubSpot %s %s -> %s: %s", resp.request.method,
                      resp.request.url, resp.status_code, resp.text[:500])
        resp.raise_for_status()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        resp = await self._client.get(path, params=params or {})
        self._raise(resp)
        return resp.json()

    async def _patch(self, path: str, properties: dict) -> dict:
        resp = await self._client.patch(path, json={"properties": properties})
        self._raise(resp)
        return resp.json() if resp.content else {}

    async def _post(self, path: str, payload: dict) -> dict:
        resp = await self._client.post(path, json=payload)
        self._raise(resp)
        return resp.json()

    # --- Companies ---
    async def get_company(self, company_id: str) -> dict:
        data = await self._get(f"/crm/v3/objects/companies/{company_id}",
                               {"properties": ",".join(COMPANY_PROPERTIES)})
        return data.get("properties", {})

    async def update_company(self, company_id: str, properties: dict) -> dict:
        return await self._patch(f"/crm/v3/objects/companies/{company_id}",
                                 {k: v for k, v in properties.items() if v not in ("", None)})

    async def find_company_by_domain(self, domain: str) -> str | None:
        data = await self._post("/crm/v3/objects/companies/search", {
            "filterGroups": [{"filters": [
                {"propertyName": "domain", "operator": "EQ", "value": domain}]}],
            "properties": ["name", "domain"], "limit": 1,
        })
        results = data.get("results", [])
        return results[0]["id"] if results else None

    async def create_company(self, properties: dict) -> str:
        data = await self._post("/crm/v3/objects/companies", {"properties": properties})
        return data["id"]

    # --- Contacts ---
    async def get_contact(self, contact_id: str) -> dict:
        data = await self._get(f"/crm/v3/objects/contacts/{contact_id}",
                               {"properties": ",".join(CONTACT_PROPERTIES)})
        return data.get("properties", {})

    async def update_contact(self, contact_id: str, properties: dict) -> dict:
        return await self._patch(f"/crm/v3/objects/contacts/{contact_id}",
                                 {k: v for k, v in properties.items() if v not in ("", None)})

    async def create_contact(self, properties: dict, company_id: str | None = None) -> str:
        payload: dict = {"properties": {k: v for k, v in properties.items() if v not in ("", None)}}
        if company_id:
            payload["associations"] = [{
                "to": {"id": company_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 1}],
            }]
        data = await self._post("/crm/v3/objects/contacts", payload)
        return data["id"]

    async def find_contact_by_linkedin(self, url: str) -> str | None:
        if not url:
            return None
        data = await self._post("/crm/v3/objects/contacts/search", {
            "filterGroups": [{"filters": [
                {"propertyName": "hs_linkedin_url", "operator": "EQ", "value": url}]}],
            "properties": ["hs_linkedin_url"], "limit": 1,
        })
        results = data.get("results", [])
        return results[0]["id"] if results else None

    async def find_contact_by_email(self, email: str) -> str | None:
        if not email:
            return None
        data = await self._post("/crm/v3/objects/contacts/search", {
            "filterGroups": [{"filters": [
                {"propertyName": "email", "operator": "EQ", "value": email}]}],
            "properties": ["email"], "limit": 1,
        })
        results = data.get("results", [])
        return results[0]["id"] if results else None

    async def company_contact_ids(self, company_id: str) -> list[str]:
        data = await self._get(f"/crm/v4/objects/companies/{company_id}/associations/contacts",
                               {"limit": 100})
        return [r["toObjectId"] for r in data.get("results", [])]
