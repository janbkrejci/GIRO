---
id: OPT-S05-T01
title: Odstranit přechodový kód HAIFA → GIRO
status: todo
depends_on: []
writes: [aifactory/, justfile, .github/workflows/, docs/, README.md, AGENTS.md, CLAUDE.md]
---

## Zadání
Přechod na GIRO proběhl (HAIFA-S12-T06). Přechodový kód už nemá komu sloužit.

Zadání. Odstranit:
1. `transition.py` (handoff posledního HAIFA dashboardu) a jeho volání ve `web/updates.py:74,166`.
2. Výchozí `janbkrejci/HAIFA` a `HAIFA_UPDATE_REPO` ve `web/updates.py:57` a varování v `environment.py:69`.
3. `home_migration.py` a příkaz migrace (`cli.py:111,3202`).
4. Přechodové vydání v CI (`.github/workflows/ci.yml`, tagy `haifa-transition-*`) a `check_transition` v `bundle/release.py`.
5. Migrační banner v dashboardu, pokud slouží jen přechodu.
6. Testy přechodu (`tests/test_transition.py`, `tests/test_giro_migration.py` a přechodové části dalších testů).
7. Dokumenty cutoveru a migrace repozitáře v `docs/rebranding/` (`cutover.md`, `repository-migration.md`, `verify_repository_migration.py`) a odkazy na ně, včetně věty o cutoveru T06 v CLAUDE.md.

Zachovat: aliasy `factory` a `HAIFA_*` (řeší S05-T13), `.factory/`, `factory/`, distribuci `aifactory`, `skill/factory`, UUID knihovny, `upgrade.py` a `upgrade_handoff.ps1` (běžný upgrade na Windows), `migration_guard.py`, historická ID úkolů.

Hotovo znamená:
- V kódu, CI a dokumentaci nezůstal odkaz na odstraněné moduly ani tagy `haifa-transition-*`.
- Testy aktualizací a upgradu projdou.
- Rozhodnutí je zapsané v `docs/decisions.md`.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
