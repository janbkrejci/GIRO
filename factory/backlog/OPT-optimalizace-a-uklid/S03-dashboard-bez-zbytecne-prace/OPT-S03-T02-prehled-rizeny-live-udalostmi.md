---
id: OPT-S03-T02
title: Přehled řízený live událostmi
status: todo
depends_on: [OPT-S03-T01, OPT-S05-T01]
---

## Zadání
Obrazovky repa už dostávají změny přes SSE (`web/src/lib/live.ts`, `LiveHub` ve `web/live.py:368`). Přehled jako jediný polluje každé 2 s (`OVERVIEW_POLL_MS`, `web/src/lib/overview.ts:19`). `FactoryOperation.vue:83` stahuje celý přehled všech rep kvůli běhům jednoho repa při každé trace události a `RepoSwitcher.vue:36` při každém otevření menu.

Zadání:
1. Globální live kanál, který agreguje události sledovaných rep. Přehled refetchuje jen po událostech `files` a `trace`. Záložní poll 15 s a obnova při fokusu zůstanou.
2. `GET /api/repos/{id}/overview` nad existujícím `Overview.repo(entry)`. `FactoryOperation` ho použije místo celého přehledu.
3. `RepoSwitcher` použije už načtená data přehledu.

Hotovo znamená:
- Vitest: přehled nemá timer 2 s a po live události refetchuje.
- Test nového endpointu, browser test přehledu projde, `just web-build`.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
