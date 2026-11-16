---
id: OPT-S01-T04
title: Rychlé hledání procesů ve worktree
status: todo
depends_on: []
---

## Zadání
`stop_directory_processes` (`worktree_processes.py:23-38`) prochází všechny procesy stroje a pro každý volá `exe()`, `cwd()` a `Path(value).resolve()`. Na Windows `cwd()` čte paměť cizího procesu a `resolve()` otevírá soubor, celkem 0,5–3 s. Běží při každém odstranění worktree (`providers/git.py:236`, `review/flow.py:734,1097,1151`).

Zadání:
1. Resolve jen kořene. Cesty procesů porovnávat přes `os.path.normcase` a `os.path.normpath` jako prefix, bez `resolve()` pro každý proces. Porovnávat s resolvovaným i původním kořenem, aby fungovaly symlinky a junctiony.
2. Ochrany zůstanou: nikdy vlastní proces a jeho rodiče, proces bez přístupu blokuje smazání.

Hotovo znamená:
- Testy: proces s cwd ve worktree se zastaví, proces mimo ne, proces v sourozenecké složce se stejným prefixem jména (`run-1` vs `run-10`) ne, vlastní proces a rodiče nikdy, proces bez přístupu vyvolá chybu.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
