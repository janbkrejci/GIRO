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

GIRO je public, default main, stejné merge volby jako HAIFA. Actions jsou před
prvním vydáním vypnuté; `GIRO_RELEASE_ENABLED=false`. Default workflow token je
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
