"""Git and file helpers shared by the tests, instead of a copy in every test module.

`git` runs git in a directory with a fixed identity and no signing; the identity comes
from ``-c`` options, so ``GIT_AUTHOR_*``/``GIT_COMMITTER_*`` set by a test still win.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_OPTIONS = ("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false")


def git(cwd: Path, *args: str, strip: bool = True) -> str:
    """Stdout of ``git args`` in `cwd` (stripped unless ``strip=False``); fails on an error."""
    out = subprocess.run(
        ["git", *_OPTIONS, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return out.strip() if strip else out


def git_raw(cwd: Path, *args: str) -> str:
    """Stdout of ``git args`` in `cwd` as git printed it (trailing newline, leading spaces)."""
    return git(cwd, *args, strip=False)


def write(root: Path, rel: str, text: str) -> Path:
    """Write `text` (LF line ends) to ``root/rel``, creating its folders."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def commit_all(repo: Path, message: str = "commit") -> str:
    """Commit every change in `repo` (an empty commit when there is none); the new HEAD."""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")
