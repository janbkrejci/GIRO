---
id: HAIFA-S13-T03
title: "Tajná plocha: vstup na macOS (CGEvent) a Linuxu (XTest)"
status: done
archived: true
workflow: build-test-review
depends_on: [HAIFA-S13-T02]
writes: [aifactory/src/aifactory/web/screen_input_macos.py, aifactory/src/aifactory/web/screen_input_x11.py, aifactory/tests/web/test_web_screen_input_platforms.py]
---

## Zadání
## Zadání

Doplň backendy vstupu z HAIFA-S13-T02 pro macOS a Linux s X11. Kontrakt z T02 zůstává beze změny:
- metody `move`, `button`, `wheel`, `key` a `release_all`,
- normalizované souřadnice,
- klávesy jako `KeyboardEvent.code`.

Výběr backendu podle platformy připravil T02. Importuje moduly níže, tady je jen vytvoříš. Pokud výběr potřebuje úpravu, nahlas to přes `needs_writes` a modul z T02 neměň.

Kde:
- `aifactory/src/aifactory/web/screen_input_macos.py` (nový): ctypes nad `ApplicationServices`/`CoreGraphics`.
  - Souřadnice v bodech přepočítá přes `CGDisplayBounds(CGMainDisplayID())`, aby Retina fungovala.
  - Myš přes `CGEventCreateMouseEvent` (pohyb, tažení se stisknutým tlačítkem jako `kCGEventLeftMouseDragged` apod.).
  - Kolečko přes `CGEventCreateScrollWheelEvent`.
  - Klávesy přes `CGEventCreateKeyboardEvent` s virtuálními keycody macOS. Modifikátory nastaví na událostech příznaky (`CGEventSetFlags`), aby fungovaly zkratky jako Cmd+C.
  - Zjistí `AXIsProcessTrusted()`. Bez oprávnění Zpřístupnění backend hlásí `permission_denied` s textem, kde oprávnění povolit, a `POST /api/screen/input` vrátí 501 s tímto důvodem.
- `aifactory/src/aifactory/web/screen_input_x11.py` (nový): ctypes nad `libX11` a `libXtst`.
  - Používá `XOpenDisplay`, `XTestFakeMotionEvent`, `XTestFakeButtonEvent` (kolečko jsou tlačítka 4–7), `XTestFakeKeyEvent` s keycodem z `XKeysymToKeycode` podle keysymů odpovídajících `code` a `XFlush`.
  - Bez `DISPLAY`, s chybějící knihovnou nebo na čistém Waylandu hlásí nepodporováno s důvodem.
- `aifactory/tests/web/test_web_screen_input_platforms.py` (nový): testy obou backendů s mockovanými ctypes funkcemi, aby běžely na libovolném OS.

Omezení:
- Žádná nová závislost (žádné pyobjc, python-xlib, pynput).
- Moduly se musí dát importovat na všech platformách, knihovny se načítají až při použití.

Mimo rozsah:
- Wayland (portal/libei),
- vícemonitorové sestavy,
- frontend.

Hotovo znamená:
- `uv run pytest tests/web/test_web_screen_input_platforms.py` projde a pokrývá pro macOS:
  - přepočet normalizovaných souřadnic na body podle `CGDisplayBounds`,
  - pohyb se stisknutým tlačítkem jako dragged událost,
  - keycode pro `KeyA`, `Enter`, `ArrowLeft` a `MetaLeft`,
  - příznak Cmd u `KeyC` při stisknutém `MetaLeft`,
  - `permission_denied` bez `AXIsProcessTrusted`.
- Pro X11 testy pokrývají:
  - pohyb na správné pixely,
  - kolečko jako tlačítka 4/5,
  - keycode z `XKeysymToKeycode`,
  - `XFlush` po dávce,
  - nepodporováno bez `DISPLAY`.
- Testy vstupu z T02 dál procházejí.
- `just lint` a `just typecheck` jsou bez chyb.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
- 2026-11-23 · workflow build-test-review · PR https://github.com/janbkrejci/GIRO/pull/23 · náklady $0.00
