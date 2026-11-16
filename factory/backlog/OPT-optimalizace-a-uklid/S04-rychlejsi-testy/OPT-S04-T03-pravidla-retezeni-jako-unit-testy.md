---
id: OPT-S04-T03
title: Pravidla řetězení jako unit testy
status: todo
depends_on: []
---

## Zadání
Sada obsahuje asi 270 plných běhů `run_task`/`run_chain` (`test_auto_continue` 40, `test_task_run` 30, `test_parallel_chain` 30, `test_auto_merge` 20, `test_branch_cleanup` 15, `test_quota_chain` 14, `test_task_auto_clean` 14, `test_live_auto_continue` 13, `test_auto_heal` 13). `run/test_auto_continue.py` má 8 variant (`test_blocked_task_*`, `test_module_switch_*`, `test_local_switch_*`, `test_legacy_step_off_*`) po 45–65 s. Každá staví repo, spouští celý engine a merguje, ačkoli ověřuje jen `select_next` a pravidla řetězu.

Zadání:
1. Rozhodnutí řetězu testovat unit testy nad načteným backlogem a falešným `TaskRunStore`.
2. Ponechat 1–2 plné řetězy na skupinu chování: auto-continue, auto-merge, CI gate (operátor a auto-merge), paralelní řetěz, kvóta.
3. Každá odstraněná varianta má unit test se stejným vstupem a očekáváním.

Hotovo znamená:
- PR obsahuje tabulku odstraněných plných testů a jejich náhrad.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
