---
id: OPT-S03-T01
title: Sdílená cache stavu repa pro přehled a obrazovky
status: todo
depends_on: [OPT-S02-T02]
---

## Zadání
Přehled (`/api/overview`) se dotazuje každé 2 s, ale cache stavu backlogu má `BACKLOG_TTL = 2.0` (`web/overview.py:134`) a `_TtlCache` ukládá čas začátku výpočtu, takže skoro vždy minou. Každý miss volá `web/backlog.py:908` `status()`: `rev-parse --show-toplevel` (kořen je známý), `rev-parse --verify` (`config/source.py:148`), `diff-index`, `ls-files --others` a případně `hash-object` (`config/status.py:88-107`). Čtení index neobnovují, takže zastaralé záznamy se hashují při každém pollu. Každý poll navíc:
- `ConfigCache._fingerprint` (`web/config_cache.py:23-51`) 2× prochází celé `.git/refs`,
- `_task_stamps` (`web/overview.py:159-187`) dělá stat každého `.md` backlogu,
- `_merged_unrecorded` (`web/backlog.py:249`) a `merge_enabled` (`run/policy.py:48`) čtou soubory pro každý task s PR,
- `RepositorySettings.show()` (`web/overview.py:948`) čte soubory nastavení.

`/config/status` (`web/app.py:1747` → `web/settings.py:95`) a `/backlog/status` cache nemají a `RepoScreen.vue:46-75` je volá při každé změně route, fokusu okna i live události. Přepnutí záložky tak stojí asi 10 procesů gitu. Stav konfigurace je implementovaný třikrát: `cli.py:1161` `_config_status`, `web/settings.py:95` `config_status`, `web/overview.py:683` `_config_view`.

Zadání:
1. Jedna cache stavu repa klíčovaná otiskem: mtime `.git/index`, `HEAD`, souboru base refu nebo `packed-refs` a otisk souborů backlogu a sdílených `.factory`. Ne celé `refs/`.
2. Při změně otisku jedno `git status` z S02-T02 místo 4–5 procesů.
3. Přehled, `/config/status`, `/backlog/status` a `/repos` čtou tuto cache.
4. Jedna implementace stavu konfigurace pro CLI i web.
5. `merge_enabled` a nastavení repa číst jednou za požadavek nebo z cache.
6. `RepoScreen.vue` neobnovuje stav při změně route, jen při live událostech a fokusu.

Hotovo znamená:
- Test ověří, že opakovaný poll přehledu bez změny v repu nespustí git.
- Po změně souboru backlogu, commitu a přepnutí větve se stav obnoví.
- Stavy `timeout` a `stale` a `REPO_TIMEOUT` fungují jako dnes.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
