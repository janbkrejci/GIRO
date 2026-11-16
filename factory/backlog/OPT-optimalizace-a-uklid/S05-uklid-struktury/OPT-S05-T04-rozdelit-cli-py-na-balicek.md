---
id: OPT-S05-T04
title: Rozdělit cli.py na balíček
status: todo
depends_on: [OPT-S05-T03]
---

## Zadání
`cli.py` (3617 řádků) je seskupený jako `_add_X_commands` a `_X` pro skupiny check, library, config, backlog, task, workflow, obs, upgrade a learn. `build_parser` má 450 řádků, `_dispatch` (`cli.py:3579`) je řetěz podmínek.

Zadání:
1. Balíček `aifactory/cli/` s modulem na skupinu příkazů a `cli/envelope.py` pro výstup `--json` (`_emit*`, dnes `cli.py:1545-1628`).
2. `_dispatch` jako tabulka.
3. Vstupní body `giro` a `factory` (`aifactory.cli:main`) beze změny.
4. Líné importy z S01-T01 zachovat.

Hotovo znamená:
- Výstup `giro --help`, `giro --skill` a obálky `--json` je shodný s dnešním.
- Test startu CLI z S01-T01 projde.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
