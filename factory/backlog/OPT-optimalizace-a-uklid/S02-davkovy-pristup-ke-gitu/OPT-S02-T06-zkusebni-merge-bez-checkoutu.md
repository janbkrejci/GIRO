---
id: OPT-S02-T06
title: Zkušební merge bez checkoutu
status: todo
depends_on: [OPT-S02-T01]
---

## Zadání
U lokálního provideru `providers/local.py:54` volá `git.trial_merge`, který přes `detached_worktree` (`providers/git.py:255-273`) dělá plný `worktree add`, `merge`, `merge --abort`, `remove_worktree` (včetně skenu procesů) a smazání složky. Děje se to pro každé otevřené PR každých 120 s (`web/pr_refresh.py:34` přes `review/sync.py:128-149`, jehož docstring „no worktree“ neplatí). `merge_commit` (`providers/git.py:278`) dělá totéž při approve a v `run/followups.py:465`.

Zadání:
1. S gitem ≥ 2.38 použít `git merge-tree --write-tree` (konflikt podle návratového kódu) a merge nebo squash commit postavit přes `commit-tree`, bez checkoutu.
2. Se starším gitem zůstane současná cesta. Verzi zjistit jednou za proces.
3. Opravit docstring v `review/sync.py`.

Hotovo znamená:
- Testy obou cest pro čistý merge, konflikt a squash. Výsledné commity (strom, rodiče, zpráva) jsou stejné jako dnes.
- Test cesty `merge-tree` se se starším gitem přeskočí, test záložní cesty běží vždy.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
