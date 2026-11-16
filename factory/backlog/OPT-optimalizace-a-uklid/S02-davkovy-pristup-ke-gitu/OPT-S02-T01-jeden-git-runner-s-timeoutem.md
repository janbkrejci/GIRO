---
id: OPT-S02-T01
title: Jeden git runner s timeoutem
status: done
depends_on: []
---

## Zadání
Git se dnes spouští přes více helperů s rozdílným chováním. Jestli může volání viset, záleží na tom, který helper volající použil:
- `run/gitops.py:22` `git` a `:58` `git_ok` bez timeoutu,
- `providers/git.py:41,48` s `procrun.limit`,
- `config/source.py:33` s `READ_ENV`, timeoutem a `GitError`,
- privátní kopie `web/review.py:264`, `web/assistant.py:452`, `web/settings.py:313`, `workflow/rebase.py:44`, `library/tree.py:44`, `library/install.py:76`.

`gh` (`providers/github.py:122` `GhCli`) nemá timeout, `az` (`providers/azure.py:140`) ano.

Zadání:
1. Jeden git runner (v `providers/git.py` nebo novém modulu) s timeoutem a volbou prostředí jen pro čtení (`GIT_OPTIONAL_LOCKS=0`). Všechny kopie mimo `engine/` ho volají. Kopie v `engine/` převede S05-T12.
2. `gh` dostane timeout stejně jako `az`.

Hotovo znamená:
- Testy selhání: visící git a visící `gh` (falešný spustitelný soubor) skončí timeoutem s typovanou chybou.
- Mimo `engine/` a runner žádný kód nevolá `subprocess` s `git`.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
