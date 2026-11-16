---
id: OPT-S04-T01
title: Pomalé testové soubory do slow tieru
status: done
depends_on: []
---

## Zadání
Tyto soubory patří mezi nejpomalejší (45–88 s na test pod zátěží), ale nejsou v `SLOW_FILES` v `tests/tiers.py`, takže `-m 'not slow'` je spouští: `review/test_ci_gate.py`, `review/test_branch_cleanup.py`, `run/test_task_publish.py`, `check/test_factory_check_system.py`. V `backlog/test_backlog_snapshot.py` je test s 1655 soubory (64 s).

Zadání:
1. Uvedené soubory přidat do `SLOW_FILES`.
2. Test s 1655 soubory přesunout do samostatného souboru a ten přidat do `SLOW_FILES`.

Hotovo znamená:
- `tests/test_tiers.py` projde a `pytest -m 'not slow' --collect-only` uvedené testy nevybere.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
