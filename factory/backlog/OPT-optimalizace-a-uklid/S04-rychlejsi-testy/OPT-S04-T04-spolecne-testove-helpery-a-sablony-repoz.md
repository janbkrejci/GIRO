---
id: OPT-S04-T04
title: Společné testové helpery a šablony repozitářů
status: todo
depends_on: [OPT-S04-T03]
---

## Zadání
`tests/repo_templates.py` (repo postavené jednou na workera a kopírované) používá jen 7 builderů. 61 souborů volá `git init` přímo. V testech je 31 kopií `def git`/`def _git`, 7× `make_repo`, 2× `init_repo`, 8× `_task`, 7× `write`.

Zadání:
1. `tests/gitutil.py` s `git()`, `write()` a `commit_all()`. Kopie nahradit.
2. Nad šablonami postavit `make_backlog_repo` (`web/backlog_fixture.py:120`), `init_repo` (`web/multi_repo.py:83`), `make_repo` (`web/test_web_factory.py:67`, `library/test_library_install_commit.py:57`, `library/test_library_config_edit.py:65`, `web/trace_fixture.py:65`, `advice_fixture.py:18`).
3. Pokud by změna přesáhla časový limit testů jednoho běhu, dokončit ji po balíčcích přes follow-up.

Hotovo znamená:
- Všechny upravené testové soubory projdou.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
