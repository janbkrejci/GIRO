# Konfigurace repa, agenti a workflow

V `.factory/` jsou `config.yaml`, `agents.yaml` (roster), volitelně `roles.yaml`, `workflows/` a `prompts/<agent>/`. Mimo Git zůstávají `local.yaml` (trace DB, `database_url`), `trace.db` a `worktrees/`. Chybějící položky repo dědí z knihovny.

**Běhy čtou sdílenou konfiguraci z commitu v base.** Změnu commitni přes **Necommitnutá konfigurace** nebo `giro config commit` (na chráněnou base `--pr`). Konfiguraci běhu ukáže `giro config show`.

## config.yaml

| Klíč | Výchozí | Význam |
| --- | --- | --- |
| `base`, `remote` | `main`, `origin` | Cílová větev a remote. |
| `git_provider` | `local` | `local`, `github` (`gh`), `azure` (`az`, sekce `azure`), `forgejo` (REST). |
| `merge_strategy` | `squash` | `squash` nebo `merge`. |
| `github_approver` | — | Jiný účet v `gh` pro schvalovací recenzi. |
| `base_changes` | `direct` | `pr` posílá změny base pull requestem. |
| `base_pr_merge` | `manual` | `auto` tyto PR rovnou schválí a sloučí. |
| `docs_root` | `factory` | Kořen `backlog/`, `specs/`, `docs/`. |
| `protected_files` | `[".factory/"]` | Cesty, které agenti nemění. |
| `max_parallel_runs` | `1` | Sloty pro souběžné tasky. |
| `stacked_prs` | `false` | Navazující PR na PR závislostí. |
| `test_timeout`, `test_slots` | 600, `0` | Časový limit a souběh testů. `0` povolí neomezený souběh, kladná hodnota zapne sdílenou frontu. |

Další klíče: `levels`, `workdir`, `worktrees_dir`, `generated`.

Azure DevOps Services sdílí HTTPS spojení pro čtení i zápisy PR. Pro vlastní servery, proxy a přihlášení jen přes Azure CLI používá GIRO `az`. Po výpadku GIRO ověří výsledek zápisu a při nejistotě ohlásí chybu.

## Záložka GIRO <!-- section:zalozka-factory -->

- **Stav repa**, **Nainstalovat GIRO**, **Stáhnout konfiguraci z base** a po instalaci **Co dál**.
- **Souhrn kontroly** a nálezy repa; vybrané opraví agent (**Vyřešit vybrané nálezy**).
- **Nastavení repozitáře:** **Sdílené nastavení repozitáře** (**Změny base**, **Merge pull requestů GIRO**, **Stacked pull requesty**) a **Lokální necommitované nastavení** (trace DB). **Uložit** zapisuje do pracovního stromu.
- **Nastavení agentů:** harness, model a přemýšlení (**zděděno**, **přepsáno**). **Upravit** a **Vrátit na zděděné**.
- **Lekce**: **Najít lekce** (`giro learn scan`) seskupí výtky běhů. **Ze všech rep** (`--all`) rozšíří hledání. Hláška odkáže na uložené lekce v příslušném repu.

## Agenti a harness

Výchozí agenti: planner, scout, builder, tester, test-reviewer, reviewer, documenter. Agent má explicitní `harness`, `model`, `thinking` a `writes`. Roster přepneš příkazem `giro config roster set claude` nebo s argumentem `codex`.

Priorita harnessu a modelu roste od rosteru přes **Harnessy tohoto počítače**, projekt, step, task a volbu pro běh až po krok workflow.

`giro config pull` funguje i během běhu. Posun base zaznamená pro guard a během posunu blokuje start běhů. Necommitované změny nebo divergence pull blokují.

## Workflow

| Workflow | Co dělá |
| --- | --- |
| `build`, `build-test` | Implementace; bez testů, nebo do zelených testů. |
| `build-review` | Implementace a review (dokumentace). |
| `build-test-review` | Implementace, testy, review; přesné zadání. |
| `plan`, `plan-build`, `plan-build-test` | Plán, případně implementace a testy. |
| `plan-build-test-quality` | Navíc lint, typy a testy celého repa. |
| `simple-sdlc` | Plán až dokumentace; velké nebo vágní tasky. |
| `scout`, `quality`, `document`, `heal` | Průzkum, kontrola repa, dokumentace, oprava sady. |
| `resolve`, `resolve-reviewed` | Konflikt PR s base. |
| `ci-fix` | Oprava červené CI v témže PR. |

Pro malou změnu použij `build-test-review`, pro velkou `simple-sdlc` nebo `plan`. Pomůže **Navrhnout workflow** a [příloha pro autory workflow](#/guide/autori-workflow).
