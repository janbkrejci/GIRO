---
id: OPT-S05-T08
title: Rozdělit review/flow.py
status: todo
depends_on: [OPT-S05-T03]
---

## Zadání
`review/flow.py` (1253 řádků) obsahuje schválení (:303-704), vrácení, publikaci a řešení konfliktů (:753-993) a úklid worktree (:993-1195), který s review nesouvisí.

Zadání:
1. Schválení, vrácení s publikací a řešením konfliktů do samostatných modulů v `review/`.
2. Úklid worktree přesunout mimo `review/` (např. `run/cleanup.py`).

Hotovo znamená:
- Testy `tests/review/` a přímých volajících projdou beze změny chování.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
