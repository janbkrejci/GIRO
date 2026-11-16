---
id: OPT-S02-T07
title: Stav PR jedním dotazem na repo
status: todo
depends_on: [OPT-S02-T01]
---

## Zadání
Synchronizace PR (`review/sync.py:128,147`) volá `provider.status()` pro každé otevřené PR každých 120 s. Na GitHubu a Azure je to proces `gh`/`az` na každé PR, s opakováním, dokud není mergeability známá. `pr_changed_files` (`run/gitops.py:204-235`) spouští 3 procesy gitu na každé otevřené PR při každém plánování fronty (`run/queue.py:896`, `review/automerge.py:313`).

Zadání:
1. GitHub: jedno `gh pr list --state all --json ...` na repo. Azure: jedno `az repos pr list` na repo. Párovat podle čísla PR.
2. Pro PR, kde seznam nevrátí potřebný údaj (např. mergeability `UNKNOWN`), zachovat dotaz na jedno PR.
3. `pr_changed_files`: merge-base cachovat podle dvojice (sha base, sha větve).

Hotovo znamená:
- Testy s falešným `gh` a `az` ověří jedno volání seznamu na repo a stejný výsledek synchronizace jako dnes.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
