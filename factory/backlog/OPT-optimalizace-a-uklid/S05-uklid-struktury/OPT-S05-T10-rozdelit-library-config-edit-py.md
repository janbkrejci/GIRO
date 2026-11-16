---
id: OPT-S05-T10
title: Rozdělit library/config_edit.py
status: done
depends_on: [OPT-S05-T03, OPT-S05-T02]
---

## Zadání
`library/config_edit.py` (1204 řádků, 38 lokálních importů) míchá plánování změn (:288-815) a jejich provedení a zápis (:815-1107).

Zadání:
1. Plán a provedení do samostatných modulů. Lokální importy převést na importy na úrovni modulu, kde to vrstvy z S05-T03 dovolí.

Hotovo znamená:
- Testy `tests/library/` a přímých volajících projdou beze změny chování.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
