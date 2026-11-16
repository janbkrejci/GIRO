---
id: OPT-S02-T02
title: Cache a dávkové git příkazy
status: done
depends_on: [OPT-S02-T01]
---

## Zadání
Git se volá po souborech a stejné dotazy se opakují. Jeden test ve `web/test_web_factory.py` spustí v průměru 165 procesů gitu, `run/test_auto_merge.py::test_merges_a_reviewed_run` 259. Nejčastější: `rev-parse --verify`, `update-index --add`, `ls-tree`, `rev-parse --show-toplevel`, `remote get-url`, `cat-file -e`.

Zadání:
1. V git vrstvě z S02-T01 cache v rámci procesu: toplevel pro cestu, ref → sha s klíčem mtime souboru refu a `packed-refs`, remote URL.
2. Dávkové příkazy:
   - stav pracovního stromu jedním `git status --porcelain=v2 -z --untracked-files=all --no-renames` s `GIT_OPTIONAL_LOCKS=0`,
   - `cat-file --batch` a `--batch-check` pro více objektů,
   - `update-index --index-info` pro více záznamů,
   - `ls-tree` pro více cest.
3. Převést volání po souborech:
   - `providers/git.py:390-395` `commit_tree_with` (`update-index` na každý soubor),
   - `cat-file -e` v `run/gitops.py:114`, `run/guard.py:95`, `library/install.py:101`, `check/context.py:124`,
   - opakované toplevel a `remote get-url` (`run/gitops.py:68,304`, `providers/git.py:62,88`, `config/source.py:139`, `review/flow.py:727`).

Hotovo znamená:
- Test ověří, že commit backlogu s N změněnými soubory spustí počet procesů gitu nezávislý na N.
- PR uvádí počet procesů gitu v `test_merges_a_reviewed_run` a v jednom testu `web/test_web_factory.py` před a po.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
