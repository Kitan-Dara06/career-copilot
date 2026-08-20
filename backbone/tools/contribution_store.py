"""Durable store for contribution opportunities + per-repo interest signals.

Keeps ``/contrib`` state between runs so the user can mark opportunities
interested / pass and the next ranking can learn from that preference.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

DEFAULT_USER = "aaliyah"


def _session_factory():
    from backbone.db.session import async_session_factory

    return async_session_factory()


def _labels(issue: dict[str, Any]) -> str:
    labels = issue.get("labels") or []
    return ",".join(labels)[:500]


def _gid(issue: dict[str, Any]) -> str:
    return f"{issue.get('repo_full_name', '')}#{issue.get('issue_number', '')}"


async def persist_opportunities(opportunities: list[dict[str, Any]]) -> None:
    """Upsert discovered opportunities so feedback has a durable target."""
    if not opportunities:
        return
    async with _session_factory()() as session:
        # Only the most-recently-discovered batch stays 'new' for the
        # /contrib button strip; prior opportunities drop to 'seen'.
        await session.execute(
            text("UPDATE contribution_opportunities SET status = 'seen' WHERE status = 'new'")
        )
        for opp in opportunities:
            await session.execute(
                text(
                    "INSERT INTO contribution_opportunities"
                    " (github_repo, github_issue_number, title, body_snippet, url,"
                    "  labels, score, skill_match, estimated_effort, status)"
                    " VALUES (:repo, :num, :title, :body, :url, :labels,"
                    "  :score, :skill, :effort, 'new')"
                    " ON CONFLICT (github_repo, github_issue_number) DO UPDATE SET"
                    "  score = EXCLUDED.score, skill_match = EXCLUDED.skill_match,"
                    "  estimated_effort = EXCLUDED.estimated_effort,"
                    "  last_seen_at = now(), status = 'new'"
                ),
                {
                    "repo": opp.get("repo_full_name", ""),
                    "num": int(opp.get("issue_number", 0)),
                    "title": (opp.get("title") or "")[:500],
                    "body": (opp.get("body") or "")[:2000],
                    "url": opp.get("url", ""),
                    "labels": _labels(opp),
                    "score": float(opp.get("_impact_score", 0.0)),
                    "skill": float(opp.get("_skill_match", 0.0)),
                    "effort": opp.get("estimated_effort", ""),
                },
            )
        await session.commit()


async def list_fresh(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most-recently-scored, not-yet-acted-on opportunities."""
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, github_repo, github_issue_number, title, url"
                " FROM contribution_opportunities"
                " WHERE status = 'new'"
                " ORDER BY score DESC, last_seen_at DESC"
                " LIMIT :limit"
            ),
            {"limit": max(1, min(int(limit), 20))},
        )
        rows = result.all()
    return [
        {
            "id": r.id,
            "gid": f"{r.github_repo}#{r.github_issue_number}",
            "title": r.title,
            "url": r.url,
        }
        for r in rows
    ]


async def record_feedback(
    gid: str,
    signal: str,
    user: str = DEFAULT_USER,
) -> bool:
    """Record interested / pass / doing for an opportunity, keyed by repo#num."""
    if signal not in {"interested", "pass", "doing"}:
        raise ValueError(f"Unknown signal: {signal}")
    try:
        async with _session_factory()() as session:
            await session.execute(
                text(
                    "DELETE FROM contribution_feedback WHERE user_id = :uid"
                    " AND opportunity_id IN (SELECT id FROM contribution_opportunities"
                    " WHERE github_repo || '#' || github_issue_number::text = :gid)"
                ),
                {"uid": user, "gid": gid},
            )
            result = await session.execute(
                text(
                    "INSERT INTO contribution_feedback (opportunity_id, user_id, signal)"
                    " SELECT id, :uid, :sig FROM contribution_opportunities"
                    " WHERE github_repo || '#' || github_issue_number::text = :gid"
                    " RETURNING id"
                ),
                {"uid": user, "sig": signal, "gid": gid},
            )
            row = result.scalar_one_or_none()
            await session.commit()
        return row is not None
    except Exception:
        return False


async def repo_signals(user: str = DEFAULT_USER) -> dict[str, dict[str, int]]:
    """Aggregate per-repo feedback counts: {repo: {interested, pass, doing}}."""
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT o.github_repo AS repo, f.signal AS signal, count(*) AS n"
                " FROM contribution_feedback f"
                " JOIN contribution_opportunities o ON o.id = f.opportunity_id"
                " WHERE f.user_id = :u"
                " GROUP BY o.github_repo, f.signal"
            ),
            {"u": user},
        )
        rows = result.all()
    signals: dict[str, dict[str, int]] = {}
    for r in rows:
        signals.setdefault(r.repo, {})[r.signal] = int(r.n)
    return signals


def apply_repo_signal(issue: dict[str, Any], signals: dict[str, dict[str, int]]) -> float:
    """Score adjustment from past interest: liked repos rise, passed repos fall."""
    repo = issue.get("repo_full_name", "")
    s = signals.get(repo, {})
    boost = 0.05 * min(s.get("interested", 0), 3) - 0.15 * min(s.get("pass", 0), 3)
    return round(boost, 2)
