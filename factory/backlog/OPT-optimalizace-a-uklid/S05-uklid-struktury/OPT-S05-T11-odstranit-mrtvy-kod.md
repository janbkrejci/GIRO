---
id: OPT-S05-T11
title: Odstranit mrtvý kód
status: todo
depends_on: []
---

## Zadání
Kód bez volajících v produkci:
- `engine/chain.py` a `engine/cli.py` (nikdo je neimportuje),
- `engine/agents.py:53` `load_config` s výchozí cestou `adws/...sssf.config.yaml`,
- `review/prbody.py:103` `run_steps` (bez volajících),
- `run/task.py:472` `task_harness_values` (volají jen testy).

Zadání:
1. Odstranit uvedený kód, jeho testy a položky v `extend-exclude` a výjimkách mypy v `pyproject.toml`.
2. Upravit test, který zakazuje `harness/` odkazovat na `engine/chain` a `engine/cli`.

Hotovo znamená:
- `just lint`, `just typecheck` a dotčené testy projdou.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
