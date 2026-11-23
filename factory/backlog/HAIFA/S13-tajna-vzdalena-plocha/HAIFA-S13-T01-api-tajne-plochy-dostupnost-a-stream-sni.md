---
id: HAIFA-S13-T01
title: "API tajné plochy: dostupnost a stream snímků obrazovky"
status: todo
workflow: build-test-review
depends_on: []
writes: [aifactory/pyproject.toml, aifactory/uv.lock, aifactory/src/aifactory/web/screen_capture.py, aifactory/src/aifactory/web/screen.py, aifactory/src/aifactory/web/routes/screen.py, aifactory/src/aifactory/web/app.py, aifactory/src/aifactory/web/routes/__init__.py, aifactory/tests/web/test_web_screen.py]
---

## Zadání
## Zadání

Přidej backend pro snímání obrazovky stroje, na kterém běží dashboard. Je to první část skryté vzdálené plochy (viz cíl stepu HAIFA-S13). Vzorem je backend tajného terminálu z HAIFA-REFINEMENT-T76 a T79 (dostupnost, PTY sezení a jejich routy). Jeho soubory jen čti, neměň je.

Kde:
- `aifactory/pyproject.toml` a `aifactory/uv.lock`: přidej závislosti `mss` (snímání na Windows, macOS i X11) a `Pillow` (zmenšení a JPEG). Přidej je přes `uv add` s mezemi verzí ve stylu ostatních závislostí.
- `aifactory/src/aifactory/web/screen_capture.py` (nový): rozhraní backendu snímání a implementace přes `mss`.
  - Backend snímá primární monitor (`monitors[1]`).
  - Hlásí jeho geometrii (`left`, `top`, `width`, `height` ve fyzických pixelech).
  - Vrací JPEG zmenšený na `max_width`, s kvalitou `quality`. Převod z BGRA dělá Pillow.
  - Zjišťuje podporu: na Linuxu bez `DISPLAY` (případně jen s `WAYLAND_DISPLAY`) vrací `supported: false` a důvod `wayland_unsupported` nebo `no_display`. Na macOS při selhání snímání kvůli oprávnění vrací důvod `permission_denied` s textem, kde oprávnění Záznam obrazovky povolit.
  - Obsahuje také falešný backend `fake`, zapnutý proměnnou prostředí `GIRO_SCREEN_BACKEND=fake`. Generuje deterministické syntetické snímky, například barevný gradient s počítadlem, a slouží jen testům.
- `aifactory/src/aifactory/web/screen.py` (nový): handlery.
  - `GET /api/screen` vrací v envelope `{enabled, supported, reason, width, height}`. `enabled` počítá stejně jako tajný terminál, tedy loopback server i klient. Jeho existující funkci `_enabled` importuj a znovu použij, nekopíruj ji.
  - `GET /api/screen/stream?fps=&max_width=&quality=` posílá binární stream rámců `[u32 big-endian délka][JPEG bajty]`. Výchozí hodnoty jsou fps 15, max_width 1280 a quality 60. Meze jsou fps 1–30, max_width 320–3840 a quality 20–95, jinak 400 `usage_error`.
  - Snímání a kódování běží ve vlákně (`anyio.to_thread`), aby neblokovalo smyčku událostí.
  - Nezměněný snímek (stejný hash surových dat) se neposílá.
  - Stream má jediného odběratele, druhý dostane 409. Odpojení klienta stream ukončí a uvolní zdroje `mss`.
  - Bez loopbacku vrací 403, bez podpory 501 `screen_unsupported` s důvodem.
- `aifactory/src/aifactory/web/routes/screen.py` (nový): `ROUTES` pro obě cesty.
- `aifactory/src/aifactory/web/app.py`: zaregistruj routy jen v aplikaci pro více repozitářů, jako terminál. Přidej stav jediného odběratele do `app.state` a úklid při ukončení aplikace.
- `aifactory/src/aifactory/web/routes/__init__.py`: popiš nové routy v docstringu vedle tajného terminálu.
- `aifactory/tests/web/test_web_screen.py` (nový): testy s mockovaným `mss` a falešným backendem.

Omezení:
- Žádný jiný snímací balíček než `mss` a `Pillow`.
- Žádné ukládání snímků na disk.
- Žádné tlačítko ani zmínka v příručce.

Mimo rozsah:
- vstup myši a klávesnice (HAIFA-S13-T02 a T03),
- frontend (T04),
- výběr jiného monitoru,
- zvuk,
- schránka.

Hotovo znamená:
- `uv run pytest tests/web/test_web_screen.py` projde a pokrývá:
  - `GET /api/screen` při loopbacku i mimo něj,
  - `supported: false` s důvodem pro Linux bez `DISPLAY`/s Waylandem (mock),
  - stream vrací rámce s délkovou hlavičkou a platným JPEG (dekódovatelný Pillow) se šířkou nejvýš `max_width`,
  - nezměněný snímek se neposílá znovu,
  - druhý odběratel dostane 409,
  - meze parametrů vrací 400,
  - bez loopbacku 403.
- Přímí volající `app.py` a `routes/__init__.py` (dohledáni v `aifactory/tests/`) procházejí.
- `just lint` a `just typecheck` jsou bez chyb (mypy strict včetně typů pro `mss`/`Pillow`).
- `uv.lock` obsahuje `mss` a `pillow`.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
