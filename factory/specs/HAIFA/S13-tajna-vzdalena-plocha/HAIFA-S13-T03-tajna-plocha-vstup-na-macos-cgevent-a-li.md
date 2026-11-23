# HAIFA-S13-T03: Vstup tajné plochy na macOS a X11

Backendy implementují kontrakt T02: `InputBackend(geometry)`, `move(x, y)`,
`button(name, down)`, `wheel(dx, dy)`, `key(code, down)` a `release_all()`.
Knihovny se načítají až při konstrukci backendu; import funguje na všech OS.
Nepřibývá závislost ani změna výběru platformy, endpointu či frontendu.

## macOS

ApplicationServices poskytuje CoreGraphics a kontrolu `AXIsProcessTrusted`.
CoreFoundation uvolňuje vytvořené události přes `CFRelease`, i při selhání
odeslání. ctypes deklaruje návratové typy ukazatelů a strukturu `CGRect`.

Normalizované souřadnice se převádějí do bodů hlavního displeje podle
`CGDisplayBounds(CGMainDisplayID())`. Hodnoty 0 a 1 označují první a poslední
bod; rozlišení snímku v pixelech na Retina displeji převod neovlivňuje.
Držené tlačítko mění pohyb na left/right/other dragged událost.

`KeyboardEvent.code` se mapuje na virtuální keycode macOS. Držené Shift,
Control, Alt a Meta se promítají do příznaků všech událostí pomocí
`CGEventSetFlags`. Obě strany modifikátoru se sledují samostatně.
Kolečko používá řádkové jednotky; kladné API hodnoty znamenají vpravo/nahoru.
macOS delty zaokrouhluje na celé řádky.

Bez Zpřístupnění vznikne `Unsupported` s důvodem `permission_denied`
a návodem na System Settings > Privacy & Security > Accessibility.
Stávající endpoint tuto chybu vrátí jako HTTP 501.

## Linux s X11

Backend načítá libX11 a libXtst, otevírá vlastní spojení přes `XOpenDisplay`
a kontroluje přítomnost XTest. Souřadnice vycházejí z geometrie zachytávání
a rozsahu prvního až posledního pixelu. Pohyb a tlačítka používají XTest;
kolečko generuje dvojice stisk/uvolnění pro tlačítka 4/5 (nahoru/dolů)
a 7/6 (vpravo/vlevo). Zlomky dílku se sčítají mezi voláními.

Kódy kláves mají odpovídající X keysym; `XKeysymToKeycode` poskytuje aktuální
keycode serveru. Pro uvolnění se zachová keycode použitý při stisku.
Po každé operaci a po úklidu se volá `XFlush`; kontrakt T02 nemá dávkový hook.
Spojení se uzavře při zániku backendu, nikoli při běžném `release_all`.

Bez `DISPLAY` je důvod `no_display`, případně `wayland_unsupported`, pokud
existuje `WAYLAND_DISPLAY`. Neotevřitelný displej znamená `no_display`;
chybějící knihovna nebo XTest znamená `input_unsupported`. Chybějící keycode
nebo selhání XTest vyvolá `OSError`, které endpoint T02 mapuje na HTTP 502.
XWayland s dostupným `DISPLAY` využívá X11; čistý Wayland se nepodporuje.

## Ověření

Mockované nativní funkce v `tests/web/test_web_screen_input_platforms.py`
ověřují souřadnice, ABI, tažení, keycody, modifikátory, scroll, úklid,
opakování neúspěšného uvolnění, chybějící oprávnění a dostupnost X11.
Test HTTP 501 používá skutečný výběr macOS backendu s mockovanými knihovnami.
Původní testy T02 zůstávají součástí ověření. Více monitorů, Wayland portal/libei
a změny frontendu jsou mimo rozsah.
