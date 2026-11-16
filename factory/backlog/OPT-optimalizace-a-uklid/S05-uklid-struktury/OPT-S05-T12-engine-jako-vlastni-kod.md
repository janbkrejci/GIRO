---
id: OPT-S05-T12
title: Engine jako vlastní kód
status: done
depends_on: [OPT-S05-T11, OPT-S02-T01]
writes: [aifactory/, justfile, CLAUDE.md, docs/decisions.md]
---

## Zadání
Pravidlo „engine zůstává diffovatelný se sssf“ už neplatí: upstream kopie `vendor/sssf` byla smazána v 81121dc2 a není s čím porovnávat. V `engine/` je 109 značek `# aifactory N:` ve 25 souborech, kód je vyjmutý z ruff a má uvolněný mypy.

Zadání:
1. Odstranit výjimky `engine/` z ruff (`extend-exclude` v `pyproject.toml`) a uvolnění v mypy. Opravit nálezy.
2. Privátní git helpery `engine/git_helper.py:9` a `engine/permissions.py:103` převést na git runner z S02-T01.
3. Odstranit značky `# aifactory N:`.
4. Z CLAUDE.md odstranit pravidlo o diffovatelnosti a značkách. Do `docs/decisions.md` zapsat, že engine je vlastní kód, a uvést původ (sssf, poslední známý upstream commit z historie repa).

Hotovo znamená:
- `just lint` a `just typecheck` projdou bez výjimek pro `engine/`.
- Testy `tests/engine/` a `tests/harness/` projdou.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
