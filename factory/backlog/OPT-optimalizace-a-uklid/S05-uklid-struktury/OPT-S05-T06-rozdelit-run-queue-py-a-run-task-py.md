---
id: OPT-S05-T06
title: Rozdělit run/queue.py a run/task.py
status: todo
depends_on: [OPT-S05-T05]
---

## Zadání
`run/queue.py` (1857 řádků) míchá výběr a priority (:283-702) s během řetězu (:702-1716, `run_chain`, `_run_parallel`, `_run_sequential`). `run/task.py` (1384 řádků) má `run_task` (:477-778) a `_execute_job` (:929-1183) po 250–300 řádcích, řešení workflow (:203-300) a čtecí helpery (:1333-1384).

Zadání:
1. `run/queue.py` rozdělit na výběr (`run/queue_select.py`) a běh řetězu (`run/chain.py`).
2. `run_task` a `_execute_job` rozdělit na pojmenované kroky.
3. Řešení workflow přesunout do `workflow/` nebo `config/`, čtecí helpery ke storu.

Hotovo znamená:
- Testy `tests/run/` a přímých volajících projdou beze změny chování.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
