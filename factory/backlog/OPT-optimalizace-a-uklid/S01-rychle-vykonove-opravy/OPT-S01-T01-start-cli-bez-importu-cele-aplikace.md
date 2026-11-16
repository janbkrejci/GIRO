---
id: OPT-S01-T01
title: Start CLI bez importu celé aplikace
status: done
depends_on: []
---

## Zadání
Každý příkaz `giro` včetně `--help` dnes načte přes 400 modulů včetně pydantic, starlette, uvicorn a rich. Na Windows kontroluje Defender každý načtený soubor. Řetězce:
- `cli.main` (`cli.py:3195`) → `migration_guard` → `upgrade` → `library.remote` (jen kvůli `version_key` a `library_min_factory_version`) → `library/__init__` (re-exporty) → `config`, `workflow` → pydantic, rich, sqlite3.
- `web/__init__.py` re-exportuje `web.app`, takže každý import `web.registry` načte Starlette a uvicorn.

Zadání:
1. `UpgradeError` a `version_key` přesunout do listového modulu (např. `aifactory/versioning.py`), který importuje jen stdlib. `migration_guard` závisí jen na něm.
2. V `upgrade.py` importovat `library.remote` až ve funkci, která ho potřebuje.
3. Vyprázdnit re-exporty ve `web/__init__.py` a přepsat volající na importy submodulů.
4. `__init__.py` balíčků `library`, `config`, `workflow`, `run`, `backlog` a `review` udělat líné (modulový `__getattr__`), nebo re-exporty odstranit a přepsat volající.

Hotovo znamená:
- Test v čistém podprocesu spustí `giro --help` a `giro --version` a ověří, že v `sys.modules` není `starlette`, `uvicorn`, `pydantic` ani `rich`.
- PR uvádí počet modulů načtených pro `giro --help` před a po.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
