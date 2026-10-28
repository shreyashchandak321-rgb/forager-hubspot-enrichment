"""Forager API client — verified against the live docs at docs.forager.ai.

Base URL: https://api-v2.forager.ai
Auth:     X-API-KEY header. Endpoints are namespaced by account id:
          /api/{account_id}/datastorage/...

Credit costs (Trial tier): organization search 1, person role search 1,
person details 1, work email 5, personal email 5, phone number 15.
Budget carefully — check the balance with GET /api/users/current/.

Note: api-v2.forager.ai sits behind Cloudflare; a browser-like User-Agent
header is required or requests are rejected (error 1010).
"""
import asyncio

import httpx

from .config import settings

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class ForagerClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.forager_base_url.rstrip("/"),
            headers={
                "X-API-KEY": settings.forager_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": _UA,
            },
            timeout=30.0,
        )
        self._acct = settings.forager_account_id

    async def _post(self, path: str, payload: dict) -> dict:
        resp = await self._client.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _get(self, path: str) -> dict:
        resp = await self._client.get(path)
        resp.raise_for_status()
        return resp.json()

    # --- Account ---------------------------------------------------------
    async def credits_balance(self) -> str:
        data = await self._get("/api/users/current/")
        accounts = data.get("accounts") or [{}]
        return (accounts[0].get("subscription") or {}).get("credits_balance", "?")

    # --- Companies ---------------------------------------------------------
    async def search_company(self, domain: str) -> dict | None:
        """Organization search by domain (1 credit). Results can be noisy —
        prefer an exact domain match with the largest headcount."""
        data = await self._post(
            f"/api/{self._acct}/datastorage/organization_search/",
            {"domains": [domain]})
        results = data.get("search_results") or []
        exact = [r for r in results if (r.get("domain") or "").lower() == domain.lower()]
        pool = exact or results
        if not pool:
            return None
        return max(pool, key=lambda r: r.get("employees_amount") or 0)

    # --- People discovery ---------------------------------------------------
    async def find_people(self, company_domain: str, pages: int = 5) -> list[dict]:
        """Person role search at a company (1 credit per call). The API
        paginates with a fixed page size of 10 and a `page` request field
        (per docs.forager.ai), so walk pages until the results run out or
        `pages` is reached. Returns raw role records."""
        out: list[dict] = []
        for page in range(1, pages + 1):
            try:
                data = await self._post(
                    f"/api/{self._acct}/datastorage/person_role_search/",
                    {"organization_domains": [company_domain],
                     "role_is_current": True, "page": page})
            except Exception:
                break  # keep whatever earlier pages returned
            batch = data.get("search_results") or []
            out.extend(batch)
            total = data.get("total_search_results") or 0
            if not batch or (total and len(out) >= total):
                break
        return out

    # --- Person enrichment ----------------------------------------------------
    async def person_detail(self, *, person_id: int | None = None,
                            linkedin_public_identifier: str = "") -> dict:
        """Full person details (1 credit): headline, description (the LinkedIn
        'About'), skills, roles, location. Does NOT include email/phone."""
        payload: dict = {}
        if person_id:
            payload["person_id"] = person_id
        if linkedin_public_identifier:
            payload["linkedin_public_identifier"] = linkedin_public_identifier
        return await self._post(
            f"/api/{self._acct}/datastorage/person_detail_lookup/", payload)

    async def work_email(self, *, first_name: str = "", last_name: str = "",
                         domain: str = "", linkedin_public_identifier: str = "") -> str:
        """Work email lookup (5 credits). Async: POST starts a job, poll GET."""
        payload = {k: v for k, v in {
            "first_name": first_name, "last_name": last_name, "domain": domain,
            "linkedin_public_identifier": linkedin_public_identifier,
        }.items() if v}
        job = await self._post(
            f"/api/v2/{self._acct}/datastorage/person_contacts_lookup/work_emails/", payload)
        job_id = job.get("id") or job.get("job_id")
        if not job_id:  # cached answers can resolve inline
            return _extract_email(job)
        for _ in range(10):
            await asyncio.sleep(3)
            state = await self._get(
                f"/api/v2/{self._acct}/datastorage/person_contacts_lookup/work_emails/{job_id}/")
            if state.get("status") in ("completed", "failed"):
                return _extract_email(state)
        return ""

    async def phone_numbers(self, *, linkedin_public_identifier: str = "",
                            person_id: int | None = None) -> str:
        """Phone number lookup (15 credits — the expensive one; call last,
        only for people whose number is still missing)."""
        payload: dict = {}
        if linkedin_public_identifier:
            payload["linkedin_public_identifier"] = linkedin_public_identifier
        if person_id:
            payload["person_id"] = person_id
        data = await self._post(
            f"/api/{self._acct}/datastorage/person_contacts_lookup/phone_numbers/", payload)
        # Verified live: the response can be a bare list of number objects,
        # not an object wrapping them — handle both shapes.
        if isinstance(data, list):
            numbers = data
        elif isinstance(data, dict):
            numbers = (data.get("phone_numbers") or data.get("results")
                       or data.get("data") or [])
            if not numbers and data.get("phone_number"):
                return str(data["phone_number"])
        else:
            numbers = []
        for item in numbers:
            if isinstance(item, dict):
                num = (item.get("phone_number") or item.get("number")
                       or item.get("value") or "")
                if num:
                    return str(num)
            elif item:
                return str(item)
        return ""


def _extract_email(payload: dict) -> str:
    """Completed work-email jobs carry a top-level `emails` array
    (per docs.forager.ai: 'every confirmed address for this person'),
    while `result` is just the string 'found'/'not_found'. The first
    version of this extractor looked inside `result` and silently
    returned '' for every lookup."""
    emails = payload.get("emails")
    if isinstance(emails, list):
        for item in emails:
            if isinstance(item, dict):
                addr = item.get("email") or item.get("address") or item.get("value")
                if addr:
                    return str(addr)
            elif item:
                return str(item)
    result = payload.get("result")
    if isinstance(result, dict):
        return result.get("email") or result.get("work_email") or ""
    return ""
