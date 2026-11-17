"""Split a markdown file into its YAML header and body."""

from __future__ import annotations

import copy
import threading

import yaml

from aifactory import yamlload

# Parsed headers by their text: the dashboard rereads whole backlogs after any one file
# changes, and YAML parsing is most of that cost. Values are copied out, never shared.
_CACHE_LIMIT = 20000
_cache: dict[str, dict[str, object]] = {}
_cache_lock = threading.Lock()


class FrontmatterError(Exception):
    """The YAML header of a markdown file is missing or invalid."""


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a markdown file into its YAML header (a mapping) and the body."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise FrontmatterError("file does not start with a '---' YAML header")
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            header = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            break
    else:
        raise FrontmatterError("YAML header is not closed with '---'")
    with _cache_lock:
        cached = _cache.get(header)
    if cached is None:
        cached = _parse_header(header)
        with _cache_lock:
            if len(_cache) >= _CACHE_LIMIT:
                _cache.clear()
            _cache[header] = cached
    return copy.deepcopy(cached), body


def _parse_header(header: str) -> dict[str, object]:
    try:
        data = yamlload.safe_load(header)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"invalid YAML in header: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterError("YAML header must be a mapping")
    return {str(k): v for k, v in data.items()}
