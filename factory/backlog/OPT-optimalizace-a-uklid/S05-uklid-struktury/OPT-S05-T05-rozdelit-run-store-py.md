---
id: OPT-S05-T05
title: Rozdělit run/store.py
status: todo
depends_on: [OPT-S05-T03]
---

## Zadání
`TaskRunStore` (`run/store.py:715-1680`) obsluhuje běhy a operace, PR (:1265), sync PR (:1334), řetězy (:1379), preference fronty (:1497), priority (:1585), kvótu (:1630) a pozici projektu (:1662). Řádkové typy jsou na :359-548. Store se vytváří na 23 místech a každé vytvoření spustí DDL schématu a 4× `PRAGMA table_info`.

Zadání:
1. Repozitáře podle oblastí nad jedním spojením. Řádkové typy do `run/rows.py`.
2. Schéma a migrace sloupců ověřit jednou za proces a cestu databáze.

Hotovo znamená:
- Testy `tests/run/` projdou.
- Schéma databáze se nemění a existující `trace.db` se otevře bez migrace.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
