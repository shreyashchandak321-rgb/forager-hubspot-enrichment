# Forager × HubSpot Enrichment Automation

Custom Python service that keeps HubSpot CRM records enriched with Forager data.

**What it does**
1. **Enriches companies** — description, revenue, headcount, industry, HQ location, LinkedIn page.
2. **Enriches contacts** — name, LinkedIn URL, job title, employer + domain, email, phone, city/state/country.
3. **Discovers contacts** at a company with **job-title filtering** (configurable keyword list).
4. **Company webhook** — a company created in HubSpot is enriched automatically; its existing contacts are enriched too, and if it has none, contacts are discovered and created.
5. **Contact webhook** — a contact created in HubSpot is enriched automatically.

Built with FastAPI. No no-code tools anywhere in the chain.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
uvicorn app.main:app --reload --port 8000
```

Environment variables (never hard-code secrets):

| Variable | What |
|---|---|
| `FORAGER_API_KEY` | From your Forager dashboard (150 free credits) |
| `FORAGER_BASE_URL` | Forager API base (default `https://api.forager.ai`) |
| `HUBSPOT_ACCESS_TOKEN` | HubSpot Private App token — scopes `crm.objects.companies.read/write`, `crm.objects.contacts.read/write` |
| `TITLE_FILTER` | Comma-separated title keywords for contact discovery |
| `WEBHOOK_SECRET` | Optional shared secret appended to webhook URLs as `?key=` |

## Deploy (Railway)

1. Push this repo to GitHub and create a Railway project from it.
2. Set the variables above in Railway → Variables.
3. Railway runs the `Procfile` (`uvicorn app.main:app --host 0.0.0.0 --port $PORT`).
4. Note your public domain, e.g. `https://<service>.up.railway.app`.

## Wire the HubSpot webhooks

In HubSpot (app/webhook settings), subscribe to:

- `company.creation` → `POST https://<domain>/webhooks/company`
- `contact.creation` → `POST https://<domain>/webhooks/contact`

Webhooks return 200 immediately; enrichment runs as a background task.
Processed object IDs are remembered in-memory so a re-delivered webhook
never spends Forager credits twice.

## Build the demo set (OpenAI)

With the service running:

```bash
curl -X POST https://<domain>/demo/openai
# or: python scripts/run_demo.py https://<domain>
```

This finds (or creates) the OpenAI company in HubSpot, enriches it, and
creates 5 title-filtered people under it with full contact details.

## Notes

- **Forager endpoints**: confirm the exact paths/response fields in the
  Forager API docs after signup — everything Forager-specific is isolated in
  `app/forager_client.py`, and the field mappers in `app/enrichment.py`
  tolerate common field-name variants.
- **Extra data**: Forager also surfaces nice-to-have person details (LinkedIn
  "About"/description, headline). The mappers capture them under `extras` in
  API responses so they can be reviewed before deciding to store them in
  HubSpot.
- **HubSpot properties**: standard properties are used where they exist
  (`annualrevenue`, `numberofemployees`, `hs_linkedin_url`, …). Create any
  missing ones under Settings → Properties.
