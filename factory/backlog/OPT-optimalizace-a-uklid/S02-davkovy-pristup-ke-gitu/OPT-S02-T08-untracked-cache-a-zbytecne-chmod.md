---
id: OPT-S02-T08
title: Untracked cache a zbytečné chmod
status: done
depends_on: []
---

## Zadání
GIRO na spravovaných repech nenastavuje `core.untrackedCache`, takže každé zjištění nesledovaných souborů prochází celý strom. `copy_library_items` (`library/inherit.py:169-201`) volá při přípravě běhu `chmod` na každý soubor skillu, což na Windows nemá účinek.

Zadání:
1. Při přípravě worktree nastavit v konfiguraci repa `core.untrackedCache=true`, jen pokud hodnota není nastavená. Existující hodnotu nikdy neměnit.
2. Na Windows nevolat `chmod` v `copy_library_items`.
3. `core.fsmonitor` nezapínat: jeho démon na Windows drží složky otevřené a blokoval by mazání worktree.

Hotovo znamená:
- Test ověří nastavení chybějící hodnoty a zachování existující.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
