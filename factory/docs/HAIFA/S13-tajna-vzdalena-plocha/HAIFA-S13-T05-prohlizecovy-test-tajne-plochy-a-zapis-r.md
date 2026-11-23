# HAIFA-S13-T05 — Prohlížečový test tajné plochy a zápis rozhodnutí

Nový browserový test spouští distribuovaný dashboard přes `dashboard` a
`make_repo`. Nastavení `GIRO_SCREEN_BACKEND=fake` platí před vytvořením aplikace,
takže snímání i vstup používají syntetický backend bez přístupu ke skutečné ploše.

Průchod otevře plochu sekvencí Shift, Control, Shift, Control. Živý getter pixelu
canvasu dovoluje opakovanému Playwright `expect` čekat na neprůhledný nenulový
pixel ve středu falešného snímku. Kliknutí do středu ověřuje skutečný POST
`/api/screen/input`, levé tlačítko, `down: true` a souřadnice 0,5/0,5 s tolerancí
0,01. Další požadavek ověřuje fyzický kód `KeyA` a stisk. Terminálová sekvence
Control, Shift, Control, Shift ponechá plochu otevřenou a terminál zavřený.
Opakování sekvence plochy ověří odeslání `release_all` a odstranění overlaye.
Test nepoužívá pevné spánky ani mockování HTTP rout.

Soubor je zařazen mezi browserové i pomalé testy. Existující test terminálu,
produkční kód a příručka zůstaly beze změny. Odstavec **Tajná plocha** vedle
**Tajný terminál** v `docs/decisions.md` popisuje ovládání, bezpečnost, SSH tunel,
API včetně limitů, rámce, platformní backendy a životní cyklus podle implementace.

Diagnostický průchod skutečným prohlížečem na Windows dne 2026-11-23:

```text
just --shell sh --shell-arg=-cu pytest tests/e2e/test_screen_browser.py -n0
1 passed in 13.86s
```

Výsledek prokazuje vykreslení syntetického snímku, vstupní požadavky, vzájemné
vyloučení overlayů a zavření s uvolněním vstupů. Neověřuje skutečný monitor ani
nativní vstup, které jsou mimo rozsah zadání. Žádná produkční chyba se neprojevila.

Samostatnému testovacímu kroku se předává `just lint`, `just typecheck` a
`just pytest tests/e2e/test_screen_browser.py tests/test_tiers.py -n0` na finálním
kódu. Na tomto Windows prostředí funguje přepnutí shellu jako v příkazu výše.
Hledání přímých volajících v celém `aifactory/tests/` našlo pro změněné množiny
v `tiers.py` kolekční hook v `conftest.py` a invarianty v `test_tiers.py`.
Testovací helpery se nemění; jejich ostatní konzumenti nepotřebují nové kontroly.
