---
id: OPT-S01-T02
title: Live watcher bez stat každého souboru
status: done
depends_on: []
---

## Zadání
`web/live.py:139-166` každých 0,5 s projde `backlog/` a `.factory/` přes `os.walk` a na každý soubor volá `Path.stat()`. Na Windows je každý `stat()` samostatné otevření souboru, tedy asi 800 za sekundu na sledované repo.

Zadání:
1. Procházet přes `os.scandir` a `DirEntry.stat()`, které na Windows bere údaje z výpisu složky bez dalšího otevření.
2. Interval sledování 1 s.

Hotovo znamená:
- Testy ověří, že změna, přidání i smazání souboru v `backlog/` a `.factory/` dál vyvolá událost.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
