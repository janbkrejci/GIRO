# GIRO — Goal in. Release out.

GIRO je lokální dashboard a CLI `giro`, které pouští kódovací agenty (Claude Code, Codex, pi) na úkoly z backlogu v tvých Git repech. Každý task běží ve vlastním worktree a větvi, projde workflow agentů (plán, implementace, testy, review) a skončí pull requestem.

## Instalace

GIRO bundle `giro-<verze>.zip` se publikuje na stránce [vydání GIRO](https://github.com/janbkrejci/GIRO/releases/latest). `factory` je kompatibilní alias, proměnné prostředí jsou jen `GIRO_*`; technické cesty a distribuce `aifactory` zůstávají.

```bash
unzip giro-<verze>.zip
cd giro-<verze>
./install.sh
giro check
giro obs
```

Úplný postup včetně prerekvizit pro macOS, Linux a Windows je v `INSTALL.md` v distribučním balíčku.

## Příručka

Uživatelská a administrační příručka je součástí dashboardu: spusť `giro obs` a klikni na ikonu knihy v horní liště. Kapitoly pokrývají první spuštění, backlog a tasky, běhy, review, konfiguraci repa, agenty a workflow, knihovnu, nastavení počítače, práci v týmu, aktualizace, příkazovou řádku a řešení problémů.

Zdroj příručky jsou markdown soubory v `src/aifactory/guide/`; instalační kapitola je zároveň `INSTALL.md` balíčku.

Agent (Claude Code, Codex, pi) dostane postupy v angličtině z `giro --skill`; výstup ulož jako jeho `SKILL.md` a po každé aktualizaci GIRO ho vygeneruj znovu.

## AI průvodce s Codexem

Průvodce podporuje Codex CLI 0.167.2 s modelem z jeho vestavěného katalogu. Dostává pouze datovou bránu pro čtení, bez shellu a souborových nástrojů. Jiné verze CLI se pro průvodce odmítnou, dokud neprojdou ověřením izolace. Claude zůstává podporovaný; pi používá dostupný Claude.

Codex průvodce má vlastní adresář `$GIRO_HOME/assistant/codex`, aby nenačítal osobní instrukce a skilly. Souborové přihlášení sdílí symlinkem na původní `auth.json`; tokeny se nekopírují. Na Windows je pro vytvoření symlinku potřeba Developer Mode. Alternativou je samostatné přihlášení: spusť `codex login` s proměnnou `CODEX_HOME` nastavenou jen pro tento příkaz na adresář průvodce. To potřebuje i uživatel systémové klíčenky. Do adresáře průvodce nepřidávej `AGENTS.md` ani `AGENTS.override.md`.

Běžné giro tasky dál používají původní prostředí Codexu, repo instrukce, skilly, shell a souborové nástroje. Omezení průvodce se na ně nepřenášejí.

## Repozitáře bez CI

GIRO při schválení nebo automatickém sloučení task PR standardně čeká až 90 sekund,
než se na hostingu objeví CI kontroly. Pokud repozitář CI nemá, přidej do sdíleného
`.factory/config.yaml` na nejvyšší úroveň:

```yaml
wait_for_ci: false
```

Výchozí hodnota je `true`; nastavení přijímá pouze boolean (bez uvozovek).
Hodnota `false` vypíná jen čekání na dosud nepřítomné kontroly. Existující běžící
kontroly se dál vyčkávají, neúspěšné kontroly dál brání sloučení a ověření HEAD
před i po načtení kontrol zůstává zachované. Použij ji pouze tam, kde neočekáváš
opožděné spuštění CI.

## Azure DevOps: rychlejší čtení

Na Azure DevOps Services GIRO automaticky čte stav a seznam PR a CI přes HTTPS
s opakovaným použitím spojení. Token získá přes Azure CLI a drží ho jen v paměti
do expirace; podporuje také `AZURE_DEVOPS_EXT_PAT`. Stav PR ani CI se necachuje,
kontroly HEAD před a po schválení zůstávají čerstvé. Zápisy (vytvoření PR,
hlasování, merge, komentáře) dál používají Azure CLI a automaticky se neopakují.

Vlastní Azure servery, proxy nebo vlastní CA nastavené přes prostředí,
`GIRO_AZ` a uložený PAT Azure CLI zachovávají CLI čtení. Pokud nelze získat
REST token, GIRO také použije CLI; tuto možnost znovu zkusí po minutě.
Selhání již autentizovaného REST čtení se hlásí jako chyba, nikdy jako prázdné CI.
HTTP čtení včetně stránek a retry sdílí limit 30 sekund; získání tokenu přes CLI
a čekání na sdílené spojení mají vlastní limity. Nejde o timeout celého schválení.

Měření na Windows / NEO_HEF: čtyři čtení stejného PR přes CLI trvala 1,53–1,85 s.
První REST čtení se získáním tokenu trvalo 1,43 s, další 0,15–0,19 s bez procesu
`az`. Jde o malý vzorek čtení metadat, nikoli o zrychlení celého tasku.

## Forgejo

V existujícím repozitáři nastav v `.factory/config.yaml` hodnotu
`git_provider: forgejo` a commitni ji do base. Pro nový repozitář použij
`giro init --provider forgejo` nebo vyber Forgejo v dashboardu. Git remote
(výchozí `origin`) musí mířit na Forgejo přes HTTPS nebo SSH.

V prostředí GIRO nebo v souboru `$GIRO_HOME/env` nastav:

```dotenv
GIRO_FORGEJO_URL=https://git.tailorservices.com
GIRO_FORGEJO_TOKEN=<token>
```

Token potřebuje oprávnění `write:repository` a `read:user`. GIRO přidává
komentáře jako recenze typu COMMENT. Git push používá přihlašovací údaje
Gitu pro daný remote. Stav CI se čte z commit statusů na HEAD pull requestu
a logy neúspěšných jobů z REST API Forgejo 17 Actions.

## Licence

GIRO je pod licencí MIT. Licence převzatých částí jsou v `THIRD_PARTY_NOTICES`.
