---
id: OPT-S05-T13
title: "Aliasy HAIFA_* na jednom místě"
status: done
depends_on: [OPT-S05-T01]
---

## Zadání
Aliasy proměnných prostředí jsou centralizované v `environment.value`, ale 27 literálů `HAIFA_*` ho obchází. Konstanty uvádějí alias jako hlavní jméno (`home.py:21` `HOME_ENV = "HAIFA_HOME"`, `library/store.py:61`, `web/server.py:29,40`, `learn/sources.py:31`). Dvojice jmen se nastavují ručně (`run/task.py:1033`, `web/assistant.py:1000`, `assistant_codex.py:166-170`). Providery čtou ještě třetí prefix `AIFACTORY_GH` a `AIFACTORY_AZ` (`providers/github.py:127`, `providers/azure.py:145`).

Zadání:
1. Kanonická jména jsou `GIRO_*`. Alias generuje jeden helper v `environment.py`, i pro proměnné předávané podprocesům.
2. Třetí prefix `AIFACTORY_*` sjednotit do stejného mechanismu.

Hotovo znamená:
- Test hlídá, že mimo `environment.py` není žádný literál `HAIFA_` ani `AIFACTORY_`.
- Stávající proměnné `HAIFA_*` dál fungují.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
