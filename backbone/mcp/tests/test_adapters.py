"""Tests for the MCP adapters and policy."""

from __future__ import annotations

from typing import Any

import httpx
import respx

from backbone.mcp.adapters import (
    discover_professors,
    load_profile,
    search_jobs,
    search_papers,
    search_professors,
    should_discover,
)
from backbone.mcp.policy import apply_policy, redact


# ── Profile ──


def test_load_profile_returns_skill_clusters() -> None:
    profile = load_profile()
    assert "skill_clusters" in profile
    assert isinstance(profile["skill_clusters"], list)
    # The seeded profile has 14 clusters, but tolerate fewer in CI.
    assert len(profile["skill_clusters"]) > 0
    first = profile["skill_clusters"][0]
    assert "name" in first and "weight" in first


def test_load_profile_has_keywords() -> None:
    profile = load_profile()
    assert isinstance(profile["keywords"], list)


def test_load_profile_has_provenance() -> None:
    profile = load_profile()
    prov = profile["provenance"]
    assert "data/user_profile.yaml" in prov["sources"]
    assert prov["retrieved_at"]
    assert len(prov["version_key"]) == 16


# ── Papers (arXiv, mocked HTTP) ──


@respx.mock
async def test_search_papers_parses_arxiv_atom() -> None:
    atom = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2501.12345v1</id>
    <title>RAG Evaluation for Agent Memory</title>
    <published>2026-08-01T00:00:00Z</published>
    <author><name>Alice Doe</name></author>
    <author><name>Bob Smith</name></author>
    <summary>We evaluate retrieval-augmented generation systems.</summary>
  </entry>
</feed>"""
    respx.get(url__startswith="https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=atom)
    )

    papers = await search_papers("RAG evaluation", limit=5)

    assert len(papers) == 1
    assert papers[0]["arxiv_id"] == "2501.12345v1"
    assert papers[0]["title"] == "RAG Evaluation for Agent Memory"
    assert papers[0]["authors"] == ["Alice Doe", "Bob Smith"]
    assert "evaluate" in papers[0]["abstract"]
    assert papers[0]["url"] == "https://arxiv.org/abs/2501.12345v1"


# ── Professors / Jobs (DB-backed, fake session) ──


class _FakeRow:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _FakeResult:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def all(self) -> list[_FakeRow]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        return _FakeResult(self._rows)


class _FakeSessionMaker:
    """Mimics the sessionmaker returned by async_session_factory()."""

    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def __call__(self) -> _FakeSession:
        return _FakeSession(self._rows)


def _patch_session_factory(monkeypatch: Any, rows: list[_FakeRow]) -> None:
    import backbone.db.session as db_session

    # async_session_factory() -> sessionmaker -> session
    monkeypatch.setattr(
        db_session,
        "async_session_factory",
        lambda: _FakeSessionMaker(rows),
    )


async def test_search_professors(monkeypatch: Any) -> None:
    _patch_session_factory(
        monkeypatch,
        [_FakeRow(name="Jane Prof", affiliation="McGill", homepage_url="http://x", added_at=None)],
    )

    rows = await search_professors("McGill")
    assert len(rows) == 1
    assert rows[0]["name"] == "Jane Prof"
    assert rows[0]["affiliation"] == "McGill"


async def test_search_professors_empty_query_returns_empty(monkeypatch: Any) -> None:
    _patch_session_factory(monkeypatch, [_FakeRow(name="X", affiliation="Y", homepage_url=None, added_at=None)])
    assert await search_professors("") == []
    assert await search_professors("   ") == []


async def test_search_jobs_filters_by_query(monkeypatch: Any) -> None:
    _patch_session_factory(
        monkeypatch,
        [_FakeRow(title="ML Engineer", organization="Acme", region="nigeria", location="Lagos", remote_ok=False, application_url="http://apply", posted_at=None)],
    )

    rows = await search_jobs(query="ML Engineer", region="nigeria")
    assert len(rows) == 1
    assert rows[0]["title"] == "ML Engineer"
    assert rows[0]["region"] == "nigeria"


async def test_search_jobs_no_filters(monkeypatch: Any) -> None:
    _patch_session_factory(
        monkeypatch,
        [_FakeRow(title="A", organization="B", region="eu", location=None, remote_ok=True, application_url=None, posted_at=None)],
    )

    rows = await search_jobs()
    assert len(rows) == 1
    assert rows[0]["remote_ok"] is True


# ── Professor discovery (CSRankings, mocked loaders) ──


def test_should_discover_heuristic() -> None:
    assert should_discover("professors at McGill doing retrieval")
    assert should_discover("find me retrieval professors")
    assert not should_discover("show my watchlist")
    assert not should_discover("")


async def test_discover_professors_filters_institution_and_area(monkeypatch: Any) -> None:
    import backbone.tools.csrankings as csk

    async def fake_institutions() -> dict:
        return {"McGill University": {"region": "CA", "country_code": "ca", "homepage": ""}}

    async def fake_author_info() -> dict:
        return {
            "rows": [
                ("sigir", "Jane McGill Prof", 5.0),   # inforet, matches hinted area
                ("acl", "Jane McGill Prof", 2.0),     # nlp, not hinted — excluded
                ("sigir", "Other Prof", 9.0),         # inforet but wrong institution
            ]
        }

    async def fake_prof_index() -> list:
        return [
            {"name": "Jane McGill Prof", "affiliation": "McGill University", "homepage": "http://j"},
            {"name": "Other Prof", "affiliation": "Stanford University", "homepage": "http://o"},
        ]

    monkeypatch.setattr(csk, "_load_institutions", fake_institutions)
    monkeypatch.setattr(csk, "_load_author_info", fake_author_info)
    monkeypatch.setattr(csk, "_load_prof_index", fake_prof_index)

    rows = await discover_professors(institution="McGill", topic="retrieval")

    assert len(rows) == 1
    assert rows[0]["name"] == "Jane McGill Prof"
    assert rows[0]["source"] == "csrankings"
    assert rows[0]["adjusted_count"] == 5.0  # only the sigir/inforet row counts


async def test_discover_professors_no_institution_returns_top_area(monkeypatch: Any) -> None:
    import backbone.tools.csrankings as csk

    async def fake_institutions() -> dict:
        return {}

    async def fake_author_info() -> dict:
        return {"rows": [("sigir", "Top IR Prof", 12.0)]}

    async def fake_prof_index() -> list:
        return [{"name": "Top IR Prof", "affiliation": "Some University", "homepage": ""}]

    monkeypatch.setattr(csk, "_load_institutions", fake_institutions)
    monkeypatch.setattr(csk, "_load_author_info", fake_author_info)
    monkeypatch.setattr(csk, "_load_prof_index", fake_prof_index)

    rows = await discover_professors(topic="retrieval")
    assert len(rows) == 1
    assert rows[0]["adjusted_count"] == 12.0


async def test_discover_professors_by_name(monkeypatch: Any) -> None:
    import backbone.tools.csrankings as csk

    async def fake_institutions() -> dict:
        return {}

    async def fake_author_info() -> dict:
        return {"rows": [("nips", "Yoshua Bengio", 60.0)]}

    async def fake_prof_index() -> list:
        return [
            {"name": "Yoshua Bengio", "affiliation": "University of Montreal", "homepage": ""},
            {"name": "Other Prof", "affiliation": "X", "homepage": ""},
        ]

    monkeypatch.setattr(csk, "_load_institutions", fake_institutions)
    monkeypatch.setattr(csk, "_load_author_info", fake_author_info)
    monkeypatch.setattr(csk, "_load_prof_index", fake_prof_index)

    rows = await discover_professors(name="Yoshua Bengio")
    assert len(rows) == 1
    assert rows[0]["name"] == "Yoshua Bengio"
    assert rows[0]["adjusted_count"] == 60.0


async def test_discover_professors_unknown_institution_returns_empty(monkeypatch: Any) -> None:
    import backbone.tools.csrankings as csk

    async def fake_institutions() -> dict:
        return {}

    async def fake_author_info() -> dict:
        return {"rows": [("sigir", "Top IR Prof", 12.0)]}

    async def fake_prof_index() -> list:
        return [{"name": "Top IR Prof", "affiliation": "Usak University", "homepage": ""}]

    monkeypatch.setattr(csk, "_load_institutions", fake_institutions)
    monkeypatch.setattr(csk, "_load_author_info", fake_author_info)
    monkeypatch.setattr(csk, "_load_prof_index", fake_prof_index)

    assert await discover_professors(institution="Usak") == []


async def test_discover_professors_name_match_is_whole_word(monkeypatch: Any) -> None:
    import backbone.tools.csrankings as csk

    async def fake_institutions() -> dict:
        return {}

    async def fake_author_info() -> dict:
        return {"rows": [("sigir", "Dimitris Plexousakis", 1.8)]}

    async def fake_prof_index() -> list:
        return [{"name": "Dimitris Plexousakis", "affiliation": "University of Crete", "homepage": ""}]

    monkeypatch.setattr(csk, "_load_institutions", fake_institutions)
    monkeypatch.setattr(csk, "_load_author_info", fake_author_info)
    monkeypatch.setattr(csk, "_load_prof_index", fake_prof_index)

    matched = await discover_professors(name="Plexousakis")
    assert len(matched) == 1
    assert matched[0]["name"] == "Dimitris Plexousakis"
    # "Usak" must not substring-match inside "Plexousakis".
    assert await discover_professors(name="Usak") == []


# ── Policy ──


def test_redact_masks_secret_shapes() -> None:
    raw = {
        "note": "use key sk-abcdefghijklmnop for auth",
        "nested": ["AIza1234567890abcdefghijklmnop"],
    }
    out = redact(raw)
    assert "sk-abcdefghijklmnop" not in out["note"]
    assert "[REDACTED]" in out["note"]


def test_apply_policy_caps_long_strings() -> None:
    result = {"text": "x" * 100}
    capped = apply_policy(result, limit=50)
    assert len(capped["text"]) == 50 + len("…[truncated]")
