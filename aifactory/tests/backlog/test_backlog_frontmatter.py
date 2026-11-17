"""YAML header of a markdown file."""

from __future__ import annotations

import pytest

from aifactory.backlog import FrontmatterError, parse_frontmatter


def test_header_and_body() -> None:
    data, body = parse_frontmatter("---\nid: A\nlist: [1, 2]\n---\n\n# Body\n")
    assert data == {"id": "A", "list": [1, 2]}
    assert body == "\n# Body\n"


def test_empty_header() -> None:
    assert parse_frontmatter("---\n---\nbody\n") == ({}, "body\n")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("id: A\n---\n", "does not start"),
        ("", "does not start"),
        ("---\nid: A\n", "not closed"),
        ("---\nid: [A\n---\n", "invalid YAML"),
        ("---\n- a\n- b\n---\n", "must be a mapping"),
    ],
)
def test_invalid(text: str, message: str) -> None:
    with pytest.raises(FrontmatterError, match=message):
        parse_frontmatter(text)


def test_a_cached_header_is_returned_as_an_independent_copy() -> None:
    text = "---\nid: X-T01\nwrites:\n  - a.py\n---\nbody\n"
    first, _ = parse_frontmatter(text)
    first["id"] = "changed"
    first["writes"].append("b.py")  # type: ignore[attr-defined]
    second, body = parse_frontmatter(text)
    assert second == {"id": "X-T01", "writes": ["a.py"]}
    assert body == "body\n"
