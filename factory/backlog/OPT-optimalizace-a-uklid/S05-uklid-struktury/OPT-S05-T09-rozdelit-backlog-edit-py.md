---
id: OPT-S05-T09
title: Rozdělit backlog/edit.py
status: done
depends_on: [OPT-S05-T03, OPT-S05-T02]
---

## Zadání
`backlog/edit.py` (1254 řádků) obsahuje úpravy tasku (:349-700), úpravy kontejnerů (:701-1014), dávku (:1026+) a sdílené zápis, zámek a commit (:127-306).

Zadání:
1. Rozdělit na moduly podle těchto oblastí. Sdílené primitivy zápisu do jednoho modulu.

Hotovo znamená:
- Testy `tests/backlog/` a přímých volajících projdou beze změny chování.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
