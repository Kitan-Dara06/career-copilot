"""CSRankings tool — fetches active CS faculty by region + area.

CSRankings.org publishes structured CSV files on the gh-pages branch of
emeryberger/CSrankings on GitHub:

  - ``csrankings-{a..z}.csv`` — one file per author-initial, schema
    ``name, affiliation, homepage, scholarid, orcid``. ~2MB total.
  - ``generated-author-info.csv`` — one row per ``name × institution × area
    × year`` with ``count`` and ``adjustedcount``. 17MB raw, but GitHub
    serves gzip-transparently so the transfer is ~4MB. Used to filter
    faculty by area (the csrankings-{a..z}.csv alone has no area tag).
  - ``institutions.csv`` — ``institution, region, countryabbrv, homepage``.
    Used to map affiliation → country → our region buckets (US/CA/EU/CN/HK).

This tool downloads them once per process lifetime (module-level cache),
filters by the requested parent areas and regions, and returns a list of
professor seed candidates ready for the verify gate.

Why this matters: paper-based discovery (S2 keyword search) is biased
toward prolific publishers — tenured senior researchers and the largest
publishing community (currently the Chinese NLP community). CSRankings
tracks faculty by *staff listing* filtered by *active publication* in
particular venues, so assistant profs at UofT / Mila / UBC enter the pool
even when they publish <5 papers per year.

Areas we care about (parent areas, used by ``CSRankingsInput.areas``):
  - ``nlp``        ← acl, emnlp, naacl
  - ``inforet``    ← sigir, www
  - ``mlmining``   ← icml, iclr, kdd, nips
  - ``ai``         ← aaai, ijcai
  - ``vision``     ← cvpr, eccv, iccv   (sometimes co-listed for multimodal)
"""

from __future__ import annotations

import asyncio
import csv
import io
from typing import Any

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext

GITHUB_BASE = "https://raw.githubusercontent.com/emeryberger/CSrankings/gh-pages"

# Conference-code → parent-area map, sourced from csrankings.js
# (https://github.com/emeryberger/CSrankings/blob/gh-pages/csrankings.js).
# We keep it as a flat dict so callers can pass either the conference key
# (e.g. ``"acl"``) or the parent area (e.g. ``"nlp"``) in ``areas`` and
# get a single set of normalised parent areas back.
CONFERENCE_TO_PARENT: dict[str, str] = {
    # AI
    "aaai": "ai",
    "ijcai": "ai",
    # Vision
    "cvpr": "vision",
    "eccv": "vision",
    "iccv": "vision",
    # ML/mining
    "icml": "mlmining",
    "iclr": "mlmining",
    "kdd": "mlmining",
    "nips": "mlmining",
    # NLP
    "acl": "nlp",
    "emnlp": "nlp",
    "naacl": "nlp",
    # IR / web
    "sigir": "inforet",
    "www": "inforet",
}

# Parent areas that are meaningful filters for our discover seed.
KNOWN_PARENT_AREAS: set[str] = {
    "nlp",
    "inforet",
    "mlmining",
    "ai",
    "vision",
}

# Country-code → our region bucket (mirrors agents.paper_tracker.agent
# _REGION_BY_COUNTRY). Kept here to keep the tool self-contained.
COUNTRY_CODE_TO_REGION: dict[str, str] = {
    "us": "US",
    "ca": "CA",
    "cn": "CN",
    "hk": "HK",
    "mo": "HK",  # Macau is bucketed with HK
    "tw": "HK",  # bucketed with HK by convention
    "gb": "UK",
}
# Country codes we treat as EU (ISO 3166-1 alpha-2).
EU_COUNTRY_CODES: set[str] = {
    "at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr",
    "de", "gr", "hu", "ie", "it", "lv", "lt", "lu", "mt", "nl",
    "pl", "pt", "ro", "sk", "si", "es", "se",
    # EFTA / de-facto-EU
    "is", "no", "ch", "li",
}

# Module-level caches. Populated on first call to ``fetch`` / ``load``, reused
# for subsequent discover runs in the same process. Each entry is the raw
# parsed CSV rows (no per-call filter) so multiple concurrent callers can
# share it.
_cache: dict[str, Any] = {}
_CACHE_LOCK = asyncio.Lock()


class CSRankingsInput(BaseModel):
    """Filter for the CSRankings seed fetch.

    ``areas`` may contain conference codes (``"acl"``) or parent area keys
    (``"nlp"``); both are normalised to parent areas and then unioned. An
    author is returned if they have ANY publication in ANY of the requested
    areas (since ``generated-author-info.csv`` is sparse across conferences).

    ``regions`` uses the same region codes used by paper_tracker's
    ``_country_to_region`` — US, CA, EU, CN, HK, UK, OTHER. Empty list → no
    region filter, return all.
    """

    areas: list[str]
    regions: list[str] = []
    # Only return profs with at least ``min_papers`` adjusted-count over the
    # full generated-author-info history. Per CSRankings' design the
    # ``adjustedcount`` field is per-paper adjusted to 1/npaper; summing gives
    # a neat "credit" number. 1.0 = one first-author paper equivalent.
    min_adjusted_count: float = 1.0


class CSRankingsProf(BaseModel):
    name: str
    affiliation: str
    homepage: str = ""
    country_code: str = ""
    region: str = ""


class CSRankingsOutput(BaseModel):
    profs: list[CSRankingsProf]
    source: str = "csrankings"


class CSRankingsTool(Tool[CSRankingsInput, CSRankingsOutput]):
    name = "csrankings.fetch_profs"
    description = (
        "Fetch active CS faculty filtered by region + area from CSRankings.org. "
        "Caches the upstream CSVs in-process for the lifetime of the agent."
    )
    input_schema = CSRankingsInput
    output_schema = CSRankingsOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_30S  # 27 small CSVs serially + one 4MB
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: CSRankingsInput) -> CSRankingsOutput:
        # Normalise areas to parent-area keys.
        normalised_areas: set[str] = set()
        for a in input.areas:
            key = a.strip().lower()
            if not key:
                continue
            if key in CONFERENCE_TO_PARENT:
                normalised_areas.add(CONFERENCE_TO_PARENT[key])
            elif key in KNOWN_PARENT_AREAS:
                normalised_areas.add(key)
            else:
                # Unknown area code — skip silently rather than blow up the
                # whole discover call. The agent's callers should validate.
                continue
        # Default: include the IR/NLP/ML/AI parent areas only. Vision is
        # optional in the call signature so we don't pull ML-adjacent people
        # by default.
        if not normalised_areas:
            normalised_areas = {"nlp", "inforet", "mlmining", "ai"}

        # Region short-circuit: if a caller passes a code not in our map
        # (e.g. "EU"), still try to honour it.
        regions_wanted = {r.strip().upper() for r in input.regions if r.strip()}

        # Load (cached) the upstream data.
        author_info = await _load_author_info()
        prof_index = await _load_prof_index()
        institutions = await _load_institutions()

        # Filter author_info to the requested areas (one pass over the 17MB
        # file's already-parsed rows).
        author_adjusted: dict[str, float] = {}
        for row_area, name, adjusted in author_info["rows"]:
            parent = CONFERENCE_TO_PARENT.get(row_area)
            if parent is None or parent not in normalised_areas:
                continue
            author_adjusted[name] = author_adjusted.get(name, 0.0) + adjusted

        # Build the set of (name, institution) tuples with region info and
        # join with the prof index for homepage + canonical institution name.
        # generated-author-info.csv uses institution names that match the
        # csrankings-{a..z}.csv affiliation field, so we join on name only.
        seen_keys: set[tuple[str, str]] = set()
        profs: list[CSRankingsProf] = []
        for prof_row in prof_index:
            name = prof_row["name"]
            affiliation = prof_row["affiliation"] or ""
            if (name, affiliation) in seen_keys or not name or not affiliation:
                continue
            adjusted = author_adjusted.get(name)
            if adjusted is None or adjusted < input.min_adjusted_count:
                continue
            inst = institutions.get(affiliation)
            country_code = (inst or {}).get("country_code", "")
            if regions_wanted:
                region = _region_for_country_code(country_code)
                if region not in regions_wanted:
                    continue
            else:
                region = _region_for_country_code(country_code)
            seen_keys.add((name, affiliation))
            profs.append(
                CSRankingsProf(
                    name=name,
                    affiliation=affiliation,
                    homepage=prof_row.get("homepage", "") or "",
                    country_code=country_code,
                    region=region,
                )
            )
        return CSRankingsOutput(profs=profs)


def _region_for_country_code(code: str) -> str:
    if not code:
        return "OTHER"
    c = code.lower()
    if c in COUNTRY_CODE_TO_REGION:
        return COUNTRY_CODE_TO_REGION[c]
    if c in EU_COUNTRY_CODES:
        return "EU"
    return "OTHER"


async def _load_author_info() -> dict[str, Any]:
    """Return a dict shaped ``{"rows": [(area, name, adjusted), ...]}``.

    We reshape the original CSV at load time so the per-call filter is O(N)
    with tiny tuples instead of O(N · dict-lookups).
    """
    if "author_info" in _cache:
        return _cache["author_info"]

    async with _CACHE_LOCK:
        if "author_info" in _cache:
            return _cache["author_info"]
        url = f"{GITHUB_BASE}/generated-author-info.csv"
        # The file is 17MB raw but GitHub serves gzip-transparently (~4MB transfer).
        rows: list[tuple[str, str, float]] = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                resp = await client.get(
                    url, timeout=30, headers={"Accept-Encoding": "gzip, deflate"}
                )
                resp.raise_for_status()
            except Exception as exc:
                raise RuntimeError(f"Failed to fetch generated-author-info.csv: {exc}") from exc
            reader = csv.DictReader(io.StringIO(resp.text))
            # Columns: name,dept,area,count,adjustedcount,year → keep (area, name, adjustedcount).
            for row in reader:
                try:
                    adjusted = float(row.get("adjustedcount") or 0.0)
                except ValueError:
                    adjusted = 0.0
                rows.append((row["area"], row["name"], adjusted))
        _cache["author_info"] = {"rows": rows, "count": len(rows)}
        return _cache["author_info"]


async def _load_prof_index() -> list[dict[str, str]]:
    """Return the merged ``csrankings-{a..z}.csv`` index as a list of dicts."""
    if "prof_index" in _cache:
        return _cache["prof_index"]

    async with _CACHE_LOCK:
        if "prof_index" in _cache:
            return _cache["prof_index"]
        # Concurrency cap on GitHub raw requests (avoid 429s when the agent
        # is also firing S2 / Tavily elsewhere).
        sem = asyncio.Semaphore(4)

        async def _fetch_initial(letter: str) -> list[dict[str, str]]:
            url = f"{GITHUB_BASE}/csrankings-{letter}.csv"
            async with sem:
                try:
                    async with httpx.AsyncClient(follow_redirects=True) as client:
                        resp = await client.get(url, timeout=15)
                        resp.raise_for_status()
                except Exception:
                    return []
                reader = csv.DictReader(io.StringIO(resp.text))
                return [r for r in reader if r.get("name")]

        results = await asyncio.gather(*[_fetch_initial(c) for c in "abcdefghijklmnopqrstuvwxyz"])
        merged: list[dict[str, str]] = []
        for chunk in results:
            merged.extend(chunk)
        _cache["prof_index"] = merged
        return _cache["prof_index"]


async def _load_institutions() -> dict[str, dict[str, str]]:
    """Return institution name → {region, country_code, homepage}."""
    if "institutions" in _cache:
        return _cache["institutions"]

    async with _CACHE_LOCK:
        if "institutions" in _cache:
            return _cache["institutions"]
        url = f"{GITHUB_BASE}/institutions.csv"
        out: dict[str, dict[str, str]] = {}
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(url, timeout=15)
                resp.raise_for_status()
            reader = csv.DictReader(io.StringIO(resp.text))
            for row in reader:
                inst_name = row.get("institution", "").strip()
                if not inst_name:
                    continue
                out[inst_name] = {
                    "region": row.get("region", "").strip(),
                    "country_code": row.get("countryabbrv", "").strip().lower(),
                    "homepage": row.get("homepage", "").strip(),
                }
        except Exception:
            out = {}
        _cache["institutions"] = out
        return _cache["institutions"]


# Auto-register.
from backbone.tools.registry import register  # noqa: E402
register(CSRankingsTool(), agent="paper_tracker")