---
id: OPT-S02-T05
title: Follow-upy bez rozbalení backlogu na disk
status: done
depends_on: [OPT-S02-T02]
---

## Zadání
Commit e6d4af27 převedl čtení commitnutého backlogu do paměti (`backlog/snapshot.py`). Zbylo zaznamenání follow-upů: `run/followups.py:552` `_record_current` volá `gitops.extract_backlog` (`run/gitops.py:131-158`), tedy `git archive` do `.tar`, rozbalení asi 400 souborů do temp složky, jejich čtení a smazání. `followups.py:454` navíc spouští `hash-object` pro každý přepsaný soubor.

Zadání:
1. Číst backlog přes `backlog/snapshot.py` v paměti, jako už to dělá `reconcile_ids`.
2. Bloby zapsat jedním procesem gitu místo jednoho na soubor.
3. `extract_backlog` odstranit, pokud mu nezůstane volající.

Hotovo znamená:
- Testy follow-upů projdou.
- Test ověří, že záznam follow-upů nevytvoří rozbalený backlog na disku a počet procesů gitu nezávisí na počtu souborů.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
