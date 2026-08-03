"""Unit-probe: _author_in_top_paper behaves correctly on synthetic clusters."""

from __future__ import annotations

from dataclasses import dataclass

from agents.paper_tracker.agent import _author_in_top_paper


@dataclass
class A:
    name: str


@dataclass
class P:
    title: str
    citation_count: int
    authors: list[A]


def main() -> None:
    # Case 1: candidate IS an author of their top paper → True.
    p1 = [
        P("Retrieval-augmented X", 500, [A("Yu Su"), A("Other Author")]),
        P("Other paper", 100, [A("Yu Su")]),
    ]
    print("case1 author-in-top:", _author_in_top_paper("Yu Su", p1))

    # Case 2: candidate's name appears in lower-ranked papers but NOT top paper.
    # S2 homonym edge case: an author with the surname in another paper but the
    # top-cited paper has no matching author name.
    p2 = [
        P("Top cited", 1000, [A("No Relation"), A("Someone Else")]),  # no Su
        P("Their own work", 60, [A("Yu Su")]),  # has Su
    ]
    print("case2 author-not-in-top:", _author_in_top_paper("Yu Su", p2))

    # Case 3: author with same surname but different first name should NOT pass
    # (homonym check).
    p3 = [
        P("Top", 1000, [A("Yu Wei"), A("Yifei Su"), A("Other")]),  # surname Su but wrong
    ]
    print("case3 homonym mismatch:", _author_in_top_paper("Yu Su", p3))

    # Case 4: abbreviated/initial-form name like "J. Ong" — author string is
    # "John Walter Ong" → match on surname + at least one initial-token alias.
    # Edge case where first name is single letter (token) gets filtered.
    p4 = [
        P("Orality and literacy", 800, [A("John Walter Ong"), A("Other")]),
    ]
    print("case4 J. Ong:", _author_in_top_paper("J. Ong", p4))

    # Case 5: empty papers list → no demotion (safety).
    print("case5 empty papers:", _author_in_top_paper("Wei Zou", []))

    # Case 6: surname-only surname match on a single-token S2 author string.
    p6 = [
        P("Top", 800, [A("Ong")]),
    ]
    print("case6 surname-only:", _author_in_top_paper("J. Ong", p6))


if __name__ == "__main__":
    main()