---
id: HAIFA-S13
title: Tajná vzdálená plocha
---

Dashboard dostane skrytý prohlížeč plochy stroje, na kterém běží (obdoba VNC). Otevírá a zavírá se sekvencí Shift, Control, Shift, Control, tedy obráceně než tajný terminál (Control, Shift, Control, Shift; HAIFA-REFINEMENT-T76 až T82). Funguje na Windows, macOS a Linuxu s X11 a ovládá se myší i klávesnicí.

## Rozhodnutí

- Přednost má rychlá odezva, ne kvalita obrazu. Snímky jsou JPEG zmenšený na nejvýš 1280 px šířky s kvalitou kolem 60 při zhruba 15 fps. Server neposílá snímek, který se nezměnil. Klient vykresluje vždy jen nejnovější snímek a nic neřadí do fronty.
- Bezpečnost je stejná jako u terminálu. Bez přihlášení, jen když jsou povolené hosty serveru i adresa klienta loopback (`server.LOOPBACK`), a platí běžná ochrana Origin, Sec-Fetch-Site a JSON. Na vzdálený stroj se uživatel dostane přes SSH tunel (`ssh -L`). Obraz ani vstup se nikam neukládají.
- Snímá se primární monitor. Souřadnice myši se v API předávají normalizovaně (0–1) vůči tomuto monitoru, takže rozdíl mezi fyzickými a logickými pixely (HiDPI, Retina) řeší až backend.
- Klávesy se posílají podle fyzické klávesy (`KeyboardEvent.code`). Rozložení klávesnice uplatní cílový systém.
- Závislosti v Pythonu jsou `mss` pro snímání a `Pillow` pro JPEG. Vstup se posílá přes ctypes do nativních API (Windows SendInput, macOS CGEvent, Linux XTest), bez pynput a pyobjc. Wayland bez X11 se nepodporuje a API vrací srozumitelný důvod. Totéž platí pro chybějící oprávnění na macOS (Záznam obrazovky, Zpřístupnění).
- Funkce zůstává tajná: žádné tlačítko, položka menu ani zmínka v příručce. Zápis do `docs/decisions.md` ano.

## Kontroly

Vždy `just lint` a `just typecheck`. K tomu cílené `just pytest` změněného chování a přímých volajících vyhledaných v celém `aifactory/tests/` a každý upravený testový soubor. Při změně webu `just web-test` a `just web-build`. Browserový test jen `just pytest tests/e2e/test_screen_browser.py -n0`. Testy nesahají na skutečnou obrazovku ani vstup: nativní API se mockují, případně se použije falešný backend.
