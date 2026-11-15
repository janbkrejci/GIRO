# T06: provozní přechod HAIFA → GIRO

Stav: probíhá; T06 ani následné T07 nejsou uzavřeny. Datum: 2026-11-15.
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
Produkční publikaci stále blokuje `GIRO_RELEASE_ENABLED=false`. Default workflow token je
read-only bez oprávnění schvalovat PR. Main chrání ruleset proti smazání a force
push. V obou repozitářích ruleset chrání v* a oba přechodové tagy před přepsáním
a smazáním; vytvoření nového tagu zůstává povolené publikační identitě.
`JanKrejciASOL` obdržel pozvánku s write; přijetí dosud není ověřené.
Inventura: žádné hooks, environments, repo secrets, deploy keys ani self-hosted
runners. Pages endpoint 404; žádná změna DNS, registries nebo cizích balíčků.

## Místní vývoj a knihovna

Checkout zůstává `/Users/jbk/Documents/HAIFA`. `origin` míří na GIRO;
`haifa-history` zachovává původní HAIFA. Upstreamy zachovaných vývojových větví
míří na GIRO. Registry ID, provozní data, `.factory/`, task ID a historie zůstávají.
Automatické pokračování a slučování jsou po dobu cutoveru pozastavené.
Další stroje zatím nebyly přepnuty ani ověřeny; jejich vlastník použije oddíl 6
[provozního postupu](repository-migration.md). HAIFA-LIBRARY zůstává na původní
adrese jako dokumentovaná kompatibilní výjimka; její data se nepřepisují.

## Vydání a ověření

Uchovaný zdroj poslední HAIFA H je výhradně
`4dd3ddf1ea18f672674123df94dc4643a4868f52` (T04), nikoli rebrandingový main.
Dosavadní latest HAIFA je `v0.1.613`; poslední úspěšný run ci.yml má číslo 613.
Před publikací H se znovu ověří vyšší verze i dostupné stabilní GIRO 1.x.

G_SHA, verze, tagy, workflow runy, digesty, instalace, přechod kliknutím a návrat
budou doplněny až podle skutečných výsledků. Žádné vydání ani živá migrace zde
zatím nejsou prohlášeny za dokončené. macOS/Linux/Windows smoke: dosud neověřeno.
Úplné `just check` a `just e2e`: čekají na výsledek. T07 zůstává zablokované T06.

## Izolovaná příprava na macOS

Z přesného H zdroje byl místně sestaven kandidát 0.1.614; nejde o zveřejněné
vydání. Instalace do samostatného UV_TOOL_DIR, UV_TOOL_BIN_DIR a HAIFA_HOME
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
Skutečně zveřejněný ZIP ještě projde samostatnou kontrolou digestu a instalace.

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
ověřoval migrační manifest a kontrolní součty. Jedno čtení trvalo 2,7 s; souběh
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
12 testů včetně obou těchto případů prošla. Závěrečné úplné brány poběží nad
neměnným checkoutem; dosavadní výsledky je nenahrazují.
