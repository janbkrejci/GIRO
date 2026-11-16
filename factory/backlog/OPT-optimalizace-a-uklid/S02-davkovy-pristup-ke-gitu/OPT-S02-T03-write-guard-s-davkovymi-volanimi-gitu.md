---
id: OPT-S02-T03
title: Write guard s dávkovými voláními gitu
status: todo
depends_on: [OPT-S02-T02]
---

## Zadání
Hlídání zápisů stojí asi 30 procesů gitu na fázi agenta a 2 na každý změněný soubor:
- před fází `run/guard.py:208` `snapshot` volá 2× `backup.capture` (`run/backup.py:173-195`), každé 7 procesů: `rev-parse HEAD`, `symbolic-ref`, `rev-parse --git-path index`, `write-tree` po kopii celého indexu do temp složky, `diff HEAD`, `diff --cached`, `ls-files --others`,
- po fázi `enforce` dalších asi 12 procesů,
- `verify` volá `_head_entry` (`run/backup.py:236-253`) pro každou změněnou cestu: `ls-tree` a `cat-file --filters`.

Index se kopíruje 4× za fázi. `git diff HEAD` běží bez `GIT_OPTIONAL_LOCKS=0` a přepisuje index hlavního checkoutu.

Zadání:
1. Špinavé cesty jedním `status` z S02-T02.
2. `_head_entry` dávkově: jedno `ls-tree -z HEAD -- <cesty>` a jedno `cat-file --batch` s filtry.
3. HEAD a větev jedním `rev-parse`, cesta k indexu z cache.
4. Kopii indexu a `write-tree` přeskočit, když se mtime a velikost indexu nezměnily.
5. Všechna čtení s `GIT_OPTIONAL_LOCKS=0`.

Hotovo znamená:
- Regresní testy guardu (zápis mimo writes, zápis do hlavního checkoutu, obnova, chráněné soubory) projdou beze změny.
- Nový test ověří, že počet procesů gitu na fázi nezávisí na počtu změněných souborů.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
