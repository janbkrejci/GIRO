---
id: HAIFA-S13-T04
title: "Overlay tajné plochy otevřený sekvencí Shift, Control, Shift, Control"
status: done
archived: true
workflow: build-test-review
depends_on: [HAIFA-S13-T02]
writes: [aifactory/web/src/lib/secretKeys.ts, aifactory/web/src/lib/secretKeys.test.ts, aifactory/web/src/lib/screen.ts, aifactory/web/src/lib/screen.test.ts, aifactory/web/src/components/screen/, aifactory/web/src/App.vue, aifactory/web/src/App.test.ts, aifactory/src/aifactory/web/static/]
---

## Zadání
## Zadání

Přidej do dashboardu skrytý celoobrazovkový prohlížeč plochy nad API z HAIFA-S13-T01 a T02.

Kde:
- `aifactory/web/src/lib/secretKeys.ts` (+ `secretKeys.test.ts`):
  - Zobecni detektor, aby rozpoznával víc sekvencí: `terminal` = Control, Shift, Control, Shift (beze změny chování) a `screen` = Shift, Control, Shift, Control.
  - Vrací název rozpoznané sekvence nebo nic. Po rozpoznání se resetují obě sekvence.
  - Pozor na překryv: sekvence pro plochu obsahuje „Control, Shift, Control“. Stisk Shift, Control, Shift, Control nesmí otevřít terminál a Control, Shift, Control, Shift nesmí otevřít plochu.
  - Zachovej limit 1 s mezi stisky a ignorování `repeat`.
- `aifactory/web/src/App.vue` (+ `App.test.ts`):
  - Sekvence `screen` přepíná `ScreenOverlay`, sekvence `terminal` jako dnes overlay terminálu.
  - Dokud je otevřený jeden overlay, druhá sekvence se ignoruje (stisky patří ovládanému stroji nebo programu v terminálu).
  - Overlay se načítá líně přes `asyncView` jako terminál.
  - Posluchač běží ve fázi capture, aby dostal modifikátory i s fokusem v overlayi.
- `aifactory/web/src/lib/screen.ts` (+ `screen.test.ts`): klient pro `GET /api/screen`, `GET /api/screen/stream` (čtení `ReadableStream` a skládání rámců `[u32 BE délka][JPEG]` napříč chunky) a `POST /api/screen/input`.
- `aifactory/web/src/components/screen/ScreenOverlay.vue` (+ `ScreenOverlay.test.ts`, případně mocky ve stejném adresáři): overlay přes celou obrazovku s `<canvas>`.
  - Vykreslování:
    - Každý rámec dekóduje `createImageBitmap(new Blob([...], {type: 'image/jpeg'}))`.
    - Kreslí jen nejnovější snímek v `requestAnimationFrame`. Starší nevykreslené snímky zahodí a uvolní (`bitmap.close()`).
    - Zachovává poměr stran (letterbox).
  - Myš:
    - `pointermove`, `pointerdown/up` (levé, prostřední, pravé tlačítko) a `wheel` převádí na normalizované souřadnice 0–1 v obrázku, mimo obrázek nic neposílá.
    - Pohyby slučuje do jednoho za snímek animace.
    - Události posílá dávkově a nejvýš jeden požadavek najednou. Co přijde během požadavku, odejde v další dávce, a z pohybů se pošle jen ten poslední.
    - Blokuje kontextové menu.
  - Klávesnice:
    - `keydown` a `keyup` posílá jako `{type: 'key', code, down}`, s `preventDefault`, aby prohlížeč nezpracoval Tab, F5, Ctrl+W, kde to jde.
    - Při ztrátě fokusu okna a při zavření pošle `release_all`.
  - Zavírá se stejnou sekvencí Shift, Control, Shift, Control a nenápadným křížkem. Zavření a odchod ze stránky zruší stream.
  - Hlášky ukazuje, když `enabled: false` (loopback, stejný text jako u terminálu), když `supported: false` (podle `reason`, včetně odkazu na oprávnění macOS) a když stream skončí (nabídne obnovení).
  - Nenápadný ukazatel fps/latence je vítaný, ale ne nutný.
- `aifactory/src/aifactory/web/static/`: přegeneruj build (`just web-build`).

Omezení:
- Žádná nová npm závislost.
- Funkce zůstává tajná: žádné tlačítko, položka menu ani zmínka v příručce.
- Chování tajného terminálu se nemění.

Mimo rozsah:
- backend,
- schránka,
- výběr monitoru,
- nastavení kvality v UI.

Hotovo znamená:
- `secretKeys.test.ts` pokrývá obě sekvence, jejich překryv v obou směrech, limit 1 s a `repeat`.
- `screen.test.ts` pokrývá skládání rámců rozdělených mezi chunky i více rámců v jednom chunku.
- `ScreenOverlay.test.ts` pokrývá:
  - přepočet souřadnic s letterboxem,
  - sloučení pohybů,
  - jediný požadavek najednou,
  - klávesu jako `code`/`down`,
  - `release_all` při blur a zavření,
  - hlášku pro `enabled: false` a `supported: false`.
- `App.test.ts` pokrývá, že sekvence `screen` otevře plochu, sekvence `terminal` terminál a že při otevřené ploše se terminál neotevře.
- `just web-test` a `just web-build` projdou a `vue-tsc --noEmit` je bez chyb.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
- 2026-11-23 · workflow build-test-review · PR https://github.com/janbkrejci/GIRO/pull/24 · náklady $0.00
