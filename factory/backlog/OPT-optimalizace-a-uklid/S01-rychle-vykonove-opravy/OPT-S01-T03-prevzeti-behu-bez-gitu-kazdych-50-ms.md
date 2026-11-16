---
id: OPT-S01-T03
title: Převzetí běhu bez gitu každých 50 ms
status: done
depends_on: []
---

## Zadání
Smyčka čekající, až spuštěný podproces převezme běh (`run/members.py:398-438`), volá `claimed()` až dvakrát za iteraci a spí 0,05 s. Každé `claimed()` jde přes `new_row`/`task_runs_for` → `existing_store` (`run/task.py:1330-1346`): `git rev-parse --show-toplevel`, čtení `local.yaml` a nový `TaskRunStore` se schématem a pragmy. Start podprocesu trvá na Windows sekundy, takže jde o desítky až stovky procesů gitu na jeden běh.

Zadání:
1. Kořen repa a store vyřešit jednou před smyčkou.
2. Interval 0,25 s (nebo `self.poll`, pokud je větší).

Hotovo znamená:
- Test ověří, že smyčka nespouští git v každé iteraci.
- Testy selhání (podproces skončí bez řádku, vypršení `claim_timeout`, převzetí existujícího běhu přes auto-continue) projdou beze změny chování.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
