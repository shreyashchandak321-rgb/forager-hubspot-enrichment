"""Enrichment orchestration: Forager data -> HubSpot records.

Mappers translate REAL Forager response shapes (verified against
docs.forager.ai) into HubSpot properties:

  organization_search  -> search_results[]: name, domain, description,
      employees_amount, finance_info.revenue, linkedin_info{industry.name,
      public_profile_url}, addresses[]{city,state,country}
  person_role_search   -> search_results[]: role_title, person{first_name,
      last_name, headline, description, location{...}, linkedin_info{...}},
      organization{name, domain, ...}
  person_detail_lookup -> person object with roles[], description, etc.
"""
import logging
import re

from .config import settings
from .forager_client import ForagerClient
from .hubspot_client import HubSpotClient

log = logging.getLogger("enrichment")

# In-memory idempotency guard: a webhook delivered twice must not spend
# Forager credits twice. (Swap for Redis/DB if the service scales out.)
_processed: set[str] = set()


def _slug_from_linkedin(url: str) -> str:
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url or "")
    return m.group(1) if m else ""


def parse_location(location: dict | None) -> tuple[str, str, str]:
    """Forager locations: {name: 'san francisco, california, united states',
    osm_locations: [{name, place_type}, ...]} -> (city, state, country)."""
    if not location:
        return "", "", ""
    city = state = country = ""
    for osm in location.get("osm_locations") or []:
        ptype = (osm.get("place_type") or "").lower()
        if not city and ("city" in ptype or "town" in ptype):
            city = osm.get("name", "")
        elif not state and ("state" in ptype or "region" in ptype or "county" in ptype):
            state = osm.get("name", "")
        elif not country and "country" in ptype:
            country = osm.get("name", "")
    if not (city and country):
        parts = [p.strip() for p in (location.get("name") or "").split(",")]
        if len(parts) >= 3:
            city, state, country = city or parts[0], state or parts[1], country or parts[-1]
        elif len(parts) == 2:
            city, country = city or parts[0], country or parts[1]
        elif parts:
            city = city or parts[0]
    return city.title(), state.title(), country.title()


def _employee_count(org: dict) -> str:
    """This portal's company 'numberofemployees' is an INTEGER property
    (verified live: HubSpot rejected a '51-200' range label with
    INVALID_INTEGER). Send the exact headcount; fall back to the lower
    bound of a range string like '51-200'."""
    n = org.get("employees_amount")
    if isinstance(n, (int, float)) and n:
        return str(int(n))
    m = re.search(r"\d+", str(org.get("employees_range") or ""))
    return m.group(0) if m else ""


# HubSpot's company 'industry' is also a fixed ENUM; a free-text LinkedIn
# industry name is rejected. Keyword-map the common ones, else omit.
_INDUSTRY_MAP = (
    (("software", "artificial intelligence", "machine learning", "ai"), "COMPUTER_SOFTWARE"),
    (("information technology", "it services"), "INFORMATION_TECHNOLOGY_AND_SERVICES"),
    (("internet",), "INTERNET"),
    (("research",), "RESEARCH"),
    (("telecom",), "TELECOMMUNICATIONS"),
    (("financial", "finance", "banking"), "FINANCIAL_SERVICES"),
    (("health", "medical", "biotech"), "HOSPITAL_HEALTH_CARE"),
    (("education", "e-learning"), "E_LEARNING"),
    (("marketing", "advertising"), "MARKETING_AND_ADVERTISING"),
)


def _industry_enum(name: str) -> str:
    n = (name or "").lower()
    for keys, enum_value in _INDUSTRY_MAP:
        if any(k in n for k in keys):
            return enum_value
    return ""


def _revenue_number(value) -> str:
    """HubSpot's 'annualrevenue' is a number property — strip currency
    symbols/text and normalise B/M/K suffixes, else omit the field."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    m = re.search(r"([\d.,]+)\s*([BMK])?", str(value).replace(",", ""))
    if not m:
        return ""
    num = float(m.group(1))
    num *= {"B": 1e9, "M": 1e6, "K": 1e3}.get(m.group(2) or "", 1)
    return str(int(num))


def map_company(org: dict) -> dict:
    addr = (org.get("addresses") or [{}])[0]
    li = org.get("linkedin_info") or {}
    return {
        # NOTE: 'name' is deliberately not written. Forager's org record
        # for openai.com is named 'ChatGPT'; overwriting the HubSpot
        # record's name with the provider's label corrupts the demo.
        "domain": org.get("domain", ""),
        "description": org.get("description", ""),
        "annualrevenue": _revenue_number((org.get("finance_info") or {}).get("revenue")),
        "numberofemployees": _employee_count(org),
        "industry": _industry_enum((li.get("industry") or {}).get("name", "")),
        "city": addr.get("city", ""),
        "state": addr.get("state", ""),
        "country": addr.get("country", ""),
        "linkedin_company_page": li.get("public_profile_url", ""),
    }


def map_role_record(record: dict) -> dict:
    """A person_role_search result -> HubSpot contact properties."""
    person = record.get("person") or {}
    org = record.get("organization") or {}
    li = person.get("linkedin_info") or {}
    city, state, country = parse_location(person.get("location"))
    return {
        "firstname": person.get("first_name", ""),
        "lastname": person.get("last_name", ""),
        "jobtitle": record.get("role_title", ""),
        "hs_linkedin_url": li.get("public_profile_url", ""),
        "company": org.get("name", ""),
        "website": org.get("domain", ""),
        "city": city, "state": state, "country": country,
        "_person_id": person.get("id"),
        "_linkedin_slug": li.get("public_identifier", ""),
        "_description": person.get("description", ""),   # LinkedIn 'About' — nice-to-have
        "_headline": person.get("headline", ""),
    }


def extras_of(mapped_or_detail: dict) -> dict:
    """Nice-to-have fields to flag in the submission notes."""
    out = {}
    for src, key in (("_description", "linkedin_about"), ("_headline", "headline"),
                     ("description", "linkedin_about"), ("headline", "headline")):
        if mapped_or_detail.get(src):
            out[key] = mapped_or_detail[src]
    return out


def public_only(mapped: dict) -> dict:
    return {k: v for k, v in mapped.items() if not k.startswith("_") and v not in ("", None)}


def title_matches(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in settings.title_keywords)


class EnrichmentService:
    def __init__(self) -> None:
        self.forager = ForagerClient()
        self.hubspot = HubSpotClient()

    # --- Scope 1 + 4: company enrichment -------------------------------
    async def enrich_company_by_id(self, company_id: str,
                                   min_contacts: int = 0) -> dict:
        if company_id in _processed and not min_contacts:
            log.info("company %s already processed, skipping", company_id)
            return {"status": "skipped-duplicate"}
        company = await self.hubspot.get_company(company_id)
        domain = company.get("domain", "")
        if not domain:
            return {"status": "no-domain"}
        org = await self.forager.search_company(domain)
        if not org:
            return {"status": "not-found-in-forager"}
        await self.hubspot.update_company(company_id, map_company(org))
        _processed.add(company_id)

        # Scope 4 (second half): existing contacts get enriched; if the
        # company has none in HubSpot, discover them. The demo build asks
        # for min_contacts=5 and tops the set up to that size.
        contact_ids = await self.hubspot.company_contact_ids(company_id)
        follow_up = ""
        if contact_ids:
            for cid in contact_ids:
                await self.enrich_contact_by_id(cid)
            follow_up = f"enriched {len(contact_ids)} existing contacts"
        if len(contact_ids) < min_contacts:
            created = await self.discover_contacts(
                domain, company_id, limit=min_contacts - len(contact_ids))
            follow_up += f"; discovered {len(created)} new contacts"
        elif not contact_ids:
            created = await self.discover_contacts(domain, company_id)
            follow_up = f"discovered {len(created)} new contacts"
        return {"status": "enriched", "company_id": company_id, "follow_up": follow_up}

    # --- Scope 2 + 5: contact enrichment -------------------------------
    async def enrich_contact_by_id(self, contact_id: str) -> dict:
        if contact_id in _processed:
            return {"status": "skipped-duplicate"}
        contact = await self.hubspot.get_contact(contact_id)
        slug = _slug_from_linkedin(contact.get("hs_linkedin_url", ""))
        updates: dict = {}
        extras: dict = {}
        if slug:
            try:
                detail = await self.forager.person_detail(linkedin_public_identifier=slug)
            except Exception as exc:  # e.g. Forager credits exhausted — degrade, don't crash
                log.warning("person_detail failed for %s: %s", contact_id, exc)
                detail = None
            if detail:
                city, state, country = parse_location(detail.get("location"))
                roles = [r for r in detail.get("roles") or [] if r.get("is_current")]
                role = roles[0] if roles else {}
                org = role.get("organization") or {}
                updates = {
                    "firstname": detail.get("first_name", ""),
                    "lastname": detail.get("last_name", ""),
                    "jobtitle": role.get("role_title", ""),
                    "company": org.get("name", ""),
                    "website": org.get("domain", ""),
                    "city": city, "state": state, "country": country,
                }
                extras = extras_of(detail)
            if not contact.get("email"):
                try:
                    email = await self.forager.work_email(linkedin_public_identifier=slug)
                except Exception as exc:
                    log.warning("work_email failed for %s: %s", contact_id, exc)
                    email = ""
                if email:
                    updates["email"] = email
            if not contact.get("phone"):
                try:
                    phone = await self.forager.phone_numbers(linkedin_public_identifier=slug)
                except Exception as exc:
                    log.warning("phone lookup failed for %s: %s", contact_id, exc)
                    phone = ""
                if phone:
                    updates["phone"] = phone
        if updates:
            await self.hubspot.update_contact(contact_id, public_only(updates))
        _processed.add(contact_id)
        return {"status": "enriched", "contact_id": contact_id, "extras": extras}

    # --- Scope 3: find contacts at a company, title-filtered ------------
    async def discover_contacts(self, domain: str, company_id: str,
                                limit: int = 5) -> list[str]:
        # Scan several pages: the title filter (leadership roles) only
        # matches a minority of records, and one page holds just 10 people.
        records = await self.forager.find_people(domain, pages=5)
        # Contacts' 'company' property should carry the HubSpot record's
        # own name (e.g. 'OpenAI'), not the provider's org label ('ChatGPT').
        hubspot_company_name = (await self.hubspot.get_company(company_id)).get("name", "")
        try:
            balance = float(await self.forager.credits_balance())
        except Exception:
            balance = 999.0
        log.info("discovery at %s: %d records, %.0f Forager credits available",
                 domain, len(records), balance)
        created: list[str] = []
        for record in records:
            if len(created) >= limit:
                break
            mapped = map_role_record(record)
            if not title_matches(mapped.get("jobtitle", "")):
                continue  # job-title filtering: only surface matching people
            if hubspot_company_name:
                mapped["company"] = hubspot_company_name
            if await self.hubspot.find_contact_by_linkedin(mapped.get("hs_linkedin_url", "")):
                continue  # already in HubSpot (e.g. from an earlier partial run)
            slug = mapped.get("_linkedin_slug", "")
            if slug:  # complete the record: email (5cr), then phone (15cr);
                # spend in cheap-to-expensive order and stop spending when
                # the trial balance can't cover the next lookup — the free
                # fields still land in HubSpot either way
                if balance >= 5:
                    try:
                        mapped["email"] = await self.forager.work_email(linkedin_public_identifier=slug)
                        balance -= 5
                    except Exception as exc:
                        log.warning("work_email failed for %s: %s", slug, exc)
                        mapped["email"] = ""
                if balance >= 15:
                    try:
                        mapped["phone"] = await self.forager.phone_numbers(linkedin_public_identifier=slug)
                        balance -= 15
                    except Exception as exc:
                        log.warning("phone lookup failed for %s: %s", slug, exc)
                        mapped["phone"] = ""
            existing = await self.hubspot.find_contact_by_email(mapped.get("email", ""))
            if existing:
                continue
            created.append(await self.hubspot.create_contact(
                public_only(mapped), company_id=company_id))
        return created
