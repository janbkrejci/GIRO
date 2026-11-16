"""Temporary git repositories with a sample ``.factory/`` (helpers, not fixtures)."""

from __future__ import annotations

from pathlib import Path

import repo_templates
from gitutil import commit_all as commit_all
from gitutil import git_raw
from gitutil import write as write

git = git_raw


AGENTS_YAML = """\
defaults:
  harness: claude
  model: sonnet
agents:
  - name: planner
    model: opus
    writes: [specs/]
  - name: builder
"""

FILES = {
    ".gitignore": ".factory/local.yaml\n",
    ".factory/config.yaml": "base: main\n",
    ".factory/agents.yaml": AGENTS_YAML,
    ".factory/prompts/planner/system.md": "You are the planner.\n",
    ".factory/prompts/planner/user.md": "Plan this: {{prompt}}\n",
    ".factory/prompts/builder/system.md": "You are the builder.\n",
    ".factory/prompts/builder/user.md": "Build this: {{prompt}}\n",
    ".factory/workflows/plan-build.yaml": "name: plan-build\nsteps: [plan, build]\n",
}


def make_repo(path: Path) -> Path:
    """A git repo on ``main`` with the sample ``.factory/`` committed."""
    repo_templates.build(path, "config", _build_repo)
    return path


def _build_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    for rel, text in FILES.items():
        write(path, rel, text)
    commit_all(path, "factory config")
