---
id: OPT-S05-T07
title: Rozdělit web/app.py a odstranit create_app
status: done
depends_on: [OPT-S05-T03, OPT-S03-T02]
---

## Zadání
`web/app.py` (3042 řádků) obsahuje handlery běhů (:732-1064), backlogu (:1139-1496), review, nastavení, knihovny a asistenta i sestavení aplikace (:2082-2867). Asi 150 rout je vypsaných v `_repo_routes()` (:1976) a `create_multi_app` (:2900-2955). `create_app` (:2164) volají jen testy (38 souborů). Globální routy se registrují dvakrát (:2195-2201 a :2925-2930). Routy `GET /backlog/task-advice/options` (:2016) a `POST /terminal/exec` (:2902) frontend nevolá.

Zadání:
1. Každý doménový modul `web/` vystaví své routy. `app.py` jen sestaví aplikaci a middleware.
2. Testy převést na `create_multi_app` (fixture s jedním repem) a `create_app` smazat.
3. Odstranit dvě nevolané routy, pokud je nevolá ani CLI nebo skill.

Hotovo znamená:
- Test porovná seznam rout (cesta, metoda) s dnešním a liší se jen o dvě odstraněné.
- Upravené testy a browser test přehledu projdou.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
