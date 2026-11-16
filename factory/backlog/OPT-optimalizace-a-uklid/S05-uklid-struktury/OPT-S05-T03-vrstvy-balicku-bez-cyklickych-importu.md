---
id: OPT-S05-T03
title: Vrstvy balíčků bez cyklických importů
status: todo
depends_on: [OPT-S01-T01, OPT-S02-T03, OPT-S02-T04, OPT-S02-T05, OPT-S02-T06, OPT-S02-T07, OPT-S05-T01]
---

## Zadání
Doménový kód sedí ve `web/` a nižší balíčky obcházejí cykly lokálními importy z `run/` (asi 25 míst, `cli.py` má 124 lokálních importů):
- `web/registry.py` (registr rep) importují `learn/sources.py:29`, `learn/scan.py:144`, `learn/accept.py:210`, `demo/__init__.py`, `cli.py`.
- `workflow/adaptive.py:93` importuje `web.backlog` (`board_state`, `_runtime`). `UsageError` se bere z `web.backlog`.
- Z `run/` importují `providers/publish.py`, `providers/git.py:198`, `config/commit.py`, `backlog/edit.py`, `backlog/commit.py`, `backlog/snapshot.py:115`, `library/install_commit.py`, `workflow/propose.py:50-53`, `workflow/adaptive.py:37-39` (privátní `run.task._main_ignored`).

Zadání:
1. `Registry`, `RepoEntry`, `RepoError`, `same_repo` a `slugify` přesunout do `aifactory/registry.py`.
2. `UsageError` do `errors.py`. `board_state` a `_runtime` mimo `web/`.
3. Infrastrukturu `run/gitops`, `run/basemoves`, `run/errors`, `run/mainwrites` a zámek `store._path_lock` přesunout do nižší vrstvy (git vrstva z S02, `aifactory/locks.py`). `_main_ignored` a `prepare_cfg` zveřejnit v `config/`.

Hotovo znamená:
- Test hlídá směr závislostí: mimo `web/` a `cli` nikdo neimportuje `aifactory.web` ani `aifactory.cli` (kromě `skill/`, který renderuje strom CLI), a `providers`, `config`, `backlog`, `library`, `workflow` neimportují `aifactory.run`.
- PR uvádí počet lokálních importů před a po.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
