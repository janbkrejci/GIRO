# T06: provozní přechod HAIFA → GIRO

Stav: T06 dokončeno; T07 zůstává otevřené. Datum: 2026-11-15.
Vlastník `janbkrejci` výslovně potvrdil název GIRO a veřejné cílové repo po
upozornění na existující AI-agent nástroj a příkaz giro na PyPI. Distribuce
zůstává aifactory; ochrana cizího příkazu při instalaci zůstává aktivní.

## Přenesená historie a nastavení

Zdroj: https://github.com/janbkrejci/HAIFA, cíl: https://github.com/janbkrejci/GIRO.
Importovaný main: `a22982988b7f87ad71c6027743108b10e2216524` (T05).
Přeneseno atomicky všech 9 větví a 236 tagů včetně jejich objektových SHA.
Manifesty před/po se shodují; zdrojové refy se nezměnily; `git fsck --full` prošel.
GitHub PR, issues a release objekty zůstaly v HAIFA. Otevřené PR/issues: žádné.
Staré pouze lokální větve včetně backup/main-before-reset-2026-10-10 zůstaly
místně a v ověřeném bundle; nebyly smazány ani zveřejněny jako nová práce.
Všechny tři místní worktrees byly čisté; neběžel žádný task ani chain.
Soukromá záloha všech místních Git refů je ověřený Git bundle. Manifesty, inventura
nastavení a provozní logy jsou lokální evidence, ne součást veřejného repozitáře.

První HTTP přenos selhal před vytvořením refů. Opakování s HTTP/1.1 a bufferem
50 MiB přeneslo celý snapshot; nebyl použit force push ani přepis historie.

GIRO je public, default main, stejné merge volby jako HAIFA. Actions byly při importu vypnuté; poté povolené pro kandidátní kontroly.
Publikaci při importu a ověřování blokovalo `GIRO_RELEASE_ENABLED=false`;
pro první vydání byl gate výslovně zapnutý. Běžné vydávání GIRO používá pouze main.
Default workflow token je
read-only bez oprávnění schvalovat PR. Main chrání ruleset proti smazání a force
push. V obou repozitářích ruleset chrání v* a oba přechodové tagy před přepsáním
a smazáním; vytvoření nového tagu zůstává povolené publikační identitě.
`JanKrejciASOL` přijal pozvánku; GitHub API ověřilo roli write a žádnou čekající pozvánku.
Inventura: žádné hooks, environments, repo secrets, deploy keys ani self-hosted
runners. Pages endpoint 404; žádná změna DNS, registries nebo cizích balíčků.

## Místní vývoj a knihovna

Při cutoveru zůstal checkout `/Users/jbk/Documents/HAIFA`; následný pokyn
vlastníka z 2026-11-16 jej přesouvá na `/Users/jbk/Documents/GIRO`. `origin` míří na GIRO;
`haifa-history` zachovává původní HAIFA. Upstreamy zachovaných vývojových větví
míří na GIRO. Registry ID, provozní data, `.factory/`, task ID a historie zůstávají.
Po dobu cutoveru bylo automatické pokračování a slučování pozastavené.
Další stroje zatím nebyly přepnuty ani ověřeny; jejich vlastník použije oddíl 6
[provozního postupu](repository-migration.md). Následné rozhodnutí
z 2026-11-16 přejmenovává knihovnu na [GIRO-LIBRARY](https://github.com/janbkrejci/GIRO-LIBRARY)
a mění výchozí URL; historie, UUID a lokální data knihovny zůstávají.

## Vydání a ověření

Uchovaný zdroj poslední HAIFA H je výhradně
`4dd3ddf1ea18f672674123df94dc4643a4868f52` (T04), nikoli rebrandingový main.
Předchozí latest HAIFA bylo `v0.1.613`; nové H má vyšší verzi 0.1.614.
Nejprve bylo vydáno a ověřeno stabilní GIRO, teprve potom poslední HAIFA.

| Vydání | Zdrojový commit | Publikační běh |
| --- | --- | --- |
| [GIRO 1.0.6](https://github.com/janbkrejci/GIRO/releases/tag/v1.0.6) | `307955985576bbd4eb8fd6aa10a2c07f05ad285e` | [43708933267](https://github.com/janbkrejci/GIRO/actions/runs/43708933267), číslo 6, attempt 1 |
| [HAIFA 0.1.614](https://github.com/janbkrejci/HAIFA/releases/tag/v0.1.614) | `4dd3ddf1ea18f672674123df94dc4643a4868f52` | [43709244609](https://github.com/janbkrejci/HAIFA/actions/runs/43709244609), číslo 614, attempt 1 |

Oba release tagy odpovídají uvedeným SHA. GIRO main se během vydání nezměnil.
Produktový kód GIRO odpovídá úplně otestovanému stromu `95205df0`; další
commity před vydáním doplnily pouze tento provozní záznam.

- `giro-1.0.6.zip`: SHA256 `8907f35edba2700476f140171f492aec2ac837965c88e1fdc7839cf89321fed3`.
- `haifa-0.1.614.zip`: SHA256 `e8198642f485ad4ed722fd46136d785b784a422b2132d8ab87c1cb0bc50247d3`.

Stažené kanonické assety se shodují s GitHub digesty; prošly také vnitřní
`SHA256SUMS`. Veřejné instalátory ověřily samostatné běhy
[GIRO](https://github.com/janbkrejci/GIRO/actions/runs/43709081913) a
[HAIFA](https://github.com/janbkrejci/GIRO/actions/runs/43709366142):

| Platforma | Veřejný GIRO instalátor | Veřejný HAIFA instalátor | Celá cesta 0.1.613 → H → G |
| --- | --- | --- | --- |
| Ubuntu latest | prošel | prošel | dosud neověřeno; T07 |
| Windows latest | prošel | prošel | dosud neověřeno; T07 |
| macOS latest (GitHub runner) | prošel | prošel | dosud neověřeno; T07 |
| Intel macOS 12.7.6 | prošel; také dashboard přes `factory obs` | prošel upgrade 0.1.613 → 0.1.614 | prošla v izolaci přes skutečné veřejné API a assety |

Na místním Macu běžný updater HAIFA 0.1.613 našel a nainstaloval H. Kliknutí
na jeho alert nainstalovalo GIRO 1.0.6, zachovalo adresu dashboardu a dokončilo
committed manifest. Výzva zmizela. Shodují se kontrolní součty souborů včetně
necommitnuté knihovny a obyčejného `-wal` souboru, kontrolní SQLite řádek i ID,
názvy a cesty registrací. Oba příkazy vracejí `GIRO 1.0.6`. Následný čerstvý
update dotaz vrátil `current`, verzi 1.0.6 a kanonický GIRO bundle z nového repo.
Tato závěrečná zkouška nepoužívala falešné odpovědi hostingu.

`just check` i všech 54 testů `just e2e` prošly; podrobnosti jsou níže.
T06 odblokovává T07, ale tato evidence sama neuzavírá celou platformní matici T07.

## Ukončení vydávání z HAIFA a provozní omezení

`HAIFA_TRANSITION_PUBLISHED=true`. Oba přechodové tagy i `v0.1.614` ukazují na
uchovaný T04 SHA. Oba workflow ve starém repo jsou `disabled_manually`, repo
Actions mají `enabled=false`; počet queued i in-progress běhů je nula.
README, popis a homepage HAIFA odkazují na GIRO; změna README je commit
`896134b55a27e9ea12e3ba0be68038249bb0f865`. Repo zůstává veřejné a nearchivované,
se všemi původními PR, issues a vydáními; poslední H zůstává latest.

Provozní instalace vlastníka nebyla nahrazena a při uzavření zkoušek stále
hlásila HAIFA 0.1.613. Ověření proběhlo v oddělených home, tool adresářích,
repozitáři a portu. Další stroje ani skutečné provozní home se zde nevydávají
za migrované. Před vlastním přechodem je nutné ukončit procesy starší než H
podle [release protokolu](release.md); instalační alias `factory` zůstává zachovaný.

Soukromé logy, manifesty, Git bundle, kandidátní instalace a jejich zálohy byly
po ukončení zkoušek přesunuty mimo registrovanou `.factory/`, aby se nestaly
součástí provozní migrační zálohy. Git bundle byl po přesunu znovu ověřen.
Dočasné testovací worktrees byly odstraněny po ověření čistoty; původní tři
worktrees a pouze místní větve zůstaly zachované. Žádné provozní databáze,
přihlašovací údaje ani přepisy agentů nejsou commitnuté.

## Izolovaná příprava na macOS

Před publikací byl z přesného H zdroje místně sestaven kandidát 0.1.614.
Instalace do samostatného UV_TOOL_DIR, UV_TOOL_BIN_DIR a HAIFA_HOME
prošla a `factory --version` vrací `factory 0.1.614`. Testovací dashboard má
vlastní port, provozní instalace vlastníka zůstává beze změny.

Starý instalátor při seedování testovacího klonu automaticky založil
HAIFA-LIBRARY PR #37. Operátor jej uzavřel bez sloučení a odstranil pouze tuto
novou testovací větev po ověření SHA. Main knihovny zůstal nezměněný. Další
zkoušky mají knihovní klon bez remote a prázdné GitHub přihlašovací prostředí.

Po publikaci každého bundlu lze spustit `release-smoke.yml` v GIRO s přesným
produktem a tagem. Tento ruční workflow ověřuje veřejný asset digest, ZIP kořen,
instalátor a CLI v izolaci na Ubuntu, Windows a macOS. Nepublikuje releasy,
nepoužívá přihlášení vlastníka a nenahrazuje zkoušku přechodu ani T07.

## Kandidát na třech platformách

[První kandidátní běh](https://github.com/janbkrejci/GIRO/actions/runs/43701812574)
prošel na Ubuntu a macOS. Windows reprodukoval chybu `UnicodeDecodeError` při
čtení českého textu v `bundle/release.py` systémovým cp1252. Oba vstupní soubory
nyní používají explicitní UTF-8. Dva nové regresní případy nejprve selhaly;
po opravě prošel celý `test_release.py` a přímý volající `test_release_workflow.py`
(21 testů), lint a mypy.

[Opravený kandidát](https://github.com/janbkrejci/GIRO/actions/runs/43702077160),
commit `f8a576d8859d0416924ea5c81e3fc5582ac9ae25`, prošel na ubuntu-latest,
windows-latest a macos-latest. Každý job sestavil kandidát 1.0.2 a ověřil jeho
instalátor i `giro --version` a `factory --version`. Samostatná větev
`codex/t06-candidate-smoke` má read-only workflow bez publikace; její workflow
se neslučuje do main. Číslo kandidáta není číslo budoucího veřejného vydání.
Následné kontroly skutečně zveřejněných ZIPů jsou uvedeny výše.

Na místním Intel macOS 12.7.6 prošla čistá kandidátní instalace GIRO 1.0.1.
Dashboard spuštěný příkazem `factory obs` hlásil `giro-dashboard`, zachoval
příkazový alias a nezobrazoval migrační alert. Před existencí GIRO releasu vrátilo
kliknutí v testovací HAIFA `update_unavailable` (HTTP 409); HAIFA i data zůstaly
beze změny. Pro skutečný přechod je připraven izolovaný registrovaný repozitář,
SQLite databáze, necommitnutá knihovna a obyčejné soubory s příponami -wal/-shm.

## Skutečný instalátor a návrat v izolaci

Zkouška používá místní odpovědi release API a kandidátní ZIPy, ale skutečný
`uv tool install`, proces dashboardu, zálohy a restart. Nejde o důkaz dostupnosti
budoucího veřejného assetu ani o migraci provozních dat vlastníka.

První přechod odhalil blokování event loopu: background monitoring synchronně
ověřoval migrační manifest a úplnost záložních souborů. Jedno čtení trvalo 2,7 s; souběh
startup kontrol bránil odpovědi health ve dvousekundovém limitu. Kontrola nyní
běží v threadpoolu, zápisy stále čekají na committed. Regrese pokrývá prepared
i committed stav a před opravou oba případy selhaly. První neúspěšný pokus navíc
vyčerpal timeout ukončení dítěte; operátor zachoval zálohu a podle ověřených
hashů obnovil pouze testovací HAIFA. Uchovaný T04 zdroj se neměnil.

Opakovaný přechod skutečně dokončil committed do GIRO; shodují se kontrolní
součty souborů včetně necommitnuté knihovny, kontrolní SQLite řádek a ID, názvy
i cesty registrovaných repozitářů. Další izolovaný pokus úmyslně ukončil GIRO
při startu: koordinátor automaticky obnovil HAIFA 0.1.614 na původním portu,
zachoval kontrolní součty a diagnostiku a odstranil rozpracovaný marker.

Prohlížeč odhalil také krátké zobrazení výzvy v GIRO před načtením verze.
Banner se nyní zobrazuje pouze pro potvrzenou verzi HAIFA 0.1; test pokrývá
čekající a neúspěšný dotaz i GIRO 1.x a 2.x. Osm nových případů před opravou
selhalo. Při úplné první kontrole prošlo 4 397 backend testů a 1 892 frontend
testů. Dva backend testy selhaly: délka příručky (text byl zkrácen) a ochrana
stale_code kvůli souběžné editaci zdrojů operátorem. Následná cílená kontrola
12 testů včetně obou těchto případů prošla. Závěrečné úplné brány pak proběhly
nad neměnným checkoutem; jejich výsledky jsou uvedeny níže.

## Závěrečné kontroly kandidátů

Opravený produktový strom `95205df0055dd82b6c916c9ce66a515d131dc0c3`
prošel [třetím kandidátním během GIRO](https://github.com/janbkrejci/GIRO/actions/runs/43704685193)
na všech třech platformách. Kandidátní commit
`50c0b458c14b1075dc3e2695748f2350416598de` se od produktového stromu liší
pouze ručním read-only workflow; tento workflow se neslučuje do main.

Také [kandidát poslední HAIFA](https://github.com/janbkrejci/GIRO/actions/runs/43705457649)
prošel instalací na Ubuntu, Windows a macOS. Build proběhl na Ubuntu z přesného
uchovaného T04 SHA, místně stampovaný na 0.1.614, bez publikace release.
Skutečná verze poslední HAIFA bude určena až jejím publikačním během.

Neměnný checkout `95205df0` dokončil `just check`: 4 403 backend testů prošlo,
40 bylo přeskočeno; 1 900 frontend testů ve 128 souborech prošlo. Mypy ověřilo
560 souborů, lint a formátování prošly (595 souborů). Celá brána trvala 2 388 s.
`just e2e` nad stejným checkoutem prošlo: všech 54 prohlížečových testů za 684 s,
včetně migrační výzvy v obou jazycích. Ani jedna úplná brána nezměnila sledované
soubory testovaného checkoutu. Následné doplnění tohoto záznamu nemění produktový kód.
