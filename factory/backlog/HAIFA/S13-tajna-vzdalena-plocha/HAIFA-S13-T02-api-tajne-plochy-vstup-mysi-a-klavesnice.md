---
id: HAIFA-S13-T02
title: "API tajné plochy: vstup myši a klávesnice a backend pro Windows"
status: todo
workflow: build-test-review
depends_on: [HAIFA-S13-T01]
writes: [aifactory/src/aifactory/web/screen_input.py, aifactory/src/aifactory/web/screen_input_windows.py, aifactory/src/aifactory/web/screen.py, aifactory/src/aifactory/web/routes/screen.py, aifactory/src/aifactory/web/routes/__init__.py, aifactory/tests/web/test_web_screen_input.py, aifactory/tests/web/test_web_routes.py]
---

## Zadání
## Zadání

Navaž na HAIFA-S13-T01 a přidej posílání vstupu myši a klávesnice na plochu stroje dashboardu. Tento task definuje kontrakt API a backend pro Windows. macOS a Linux doplní HAIFA-S13-T03.

Kde:
- `aifactory/src/aifactory/web/screen_input.py` (nový):
  - Validuje události a definuje rozhraní backendu vstupu: `move(x, y)`, `button(name, down)`, `wheel(dx, dy)`, `key(code, down)` a `release_all()`.
  - Vybírá backend podle platformy. Pro `GIRO_SCREEN_BACKEND=fake` použije falešný backend, který události jen zaznamená do paměti.
  - Výběr pro macOS a Linux rovnou připrav. Bude importovat moduly `aifactory.web.screen_input_macos` a `aifactory.web.screen_input_x11` (vzniknou v T03). Do té doby ošetři `ImportError`: platforma se hlásí jako nepodporovaná.
  - Obsahuje tabulku podporovaných `KeyboardEvent.code`: písmena, číslice, F1–F12, šipky, Enter, Escape, Backspace, Tab, mezerník, Delete, Insert, Home, End, PageUp, PageDown, modifikátory vlevo i vpravo (včetně Meta/OS), interpunkce na US pozicích a numerická klávesnice.
  - Pamatuje si stisknuté klávesy a tlačítka, aby je `release_all()` uvolnil.
- `aifactory/src/aifactory/web/screen_input_windows.py` (nový): implementace přes ctypes `user32.SendInput`.
  - Myš: `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`, normalizované souřadnice přepočtené na 0–65535 přes virtuální plochu podle geometrie primárního monitoru z T01.
  - Tlačítka left/middle/right a kolečko (`MOUSEEVENTF_WHEEL`/`HWHEEL`).
  - Klávesy jako scancody (`KEYEVENTF_SCANCODE`, u rozšířených kláves `KEYEVENTF_EXTENDEDKEY`).
  - Modul se musí dát importovat i mimo Windows. Ctypes se nahrávají až při použití.
- `aifactory/src/aifactory/web/screen.py`: přidej handler `POST /api/screen/input` s tělem `{events: [...]}`, nejvýš 200 událostí.
  - Událost je jedna z `{type: 'move', x, y}`, `{type: 'button', button: 'left'|'middle'|'right', down, x, y}`, `{type: 'wheel', dx, dy}`, `{type: 'key', code, down}` a `{type: 'release_all'}`.
  - `x` a `y` jsou čísla 0–1 vůči primárnímu monitoru.
  - Neznámý `code` se přeskočí a vrátí v `ignored`. Odpověď je `{applied, ignored}`.
  - Platí loopback (403) a write guard. Neplatné tělo vrací 400 `usage_error`, nepodporovaná platforma 501.
  - Když stream z T01 skončí (odpojení klienta), zavolá se `release_all()`, aby nezůstala stisknutá klávesa.
- `aifactory/src/aifactory/web/routes/screen.py`: přidej routu.
- `aifactory/src/aifactory/web/routes/__init__.py`: doplň docstring.
- `aifactory/tests/web/test_web_screen_input.py` (nový): testy s falešným backendem a s mockovaným `SendInput`.

Omezení:
- Žádná nová závislost, jen ctypes.
- Vstup se nikam neukládá ani neloguje (kvůli heslům).

Mimo rozsah:
- implementace pro macOS a Linux (T03),
- frontend (T04),
- vkládání textu přes IME,
- schránka.

Hotovo znamená:
- `uv run pytest tests/web/test_web_screen_input.py tests/web/test_web_screen.py` projde a pokrývá:
  - validaci (meze 0–1, nejvýš 200 událostí, neznámý typ vrací 400),
  - neznámý `code` v `ignored`,
  - falešný backend dostane události ve správném pořadí,
  - konec streamu volá `release_all()`,
  - bez loopbacku 403,
  - Windows backend s mockovaným `SendInput` dostane správné příznaky a absolutní souřadnice (např. střed monitoru dá 32767/32768 v rámci virtuální plochy) a správné scancody pro `KeyA`, `Enter`, `ArrowLeft` (rozšířená) a `ControlRight`,
  - chybějící modul platformy se hlásí jako nepodporováno.
- `just lint` a `just typecheck` jsou bez chyb.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
