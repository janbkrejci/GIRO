# HAIFA-S13-T02: Implementace vstupu tajné plochy

Dashboard přijímá dávky myši a klávesnice přes `POST /api/screen/input`. Kontrakt, směry kolečka a chybové kódy popisuje [specifikace](../../../specs/HAIFA/S13-tajna-vzdalena-plocha/HAIFA-S13-T02-api-tajne-plochy-vstup-mysi-a-klavesnice.md).

Validace celé dávky předchází odesílání vstupu. Neznámé klávesové kódy se přeskakují a vracejí v `ignored`. Zámek sdíleného vstupního stavu zajišťuje pořadí dávky a chrání cleanup před souběžným odesíláním. Uvolněné klávesy a tlačítka se ze stavu odstraňují; selhání uvolnění ponechá vstup pro další pokus. Odpojení streamu a ukončení jeho session uvolňují držené vstupy.

Windows implementace sestavuje ABI SendInput pomocí ctypes s pevnými 32bitovými poli a ukazatelem odpovídajícím procesu. Pozice primárního monitoru pochází ze zachytávání T01, rozsah virtuální plochy z GetSystemMetrics. Tlačítka a obě osy kolečka používají příznaky myši, klávesy fyzické scancody včetně rozšířených kláves. Modul lze importovat bez Windows; user32 se načítá až při konstrukci backendu.

Testy neovládají skutečnou plochu. Používají paměťový backend nebo mock SendInput a ověřují validaci, pořadí, limity, loopback, write guard, chybějící platformní modul, cleanup a Windows struktury, souřadnice, příznaky a scancody.

V builder kroku prošel jediný cílený diagnostický test `tests/web/test_web_screen_input.py::test_windows_edges_buttons_wheel_and_scancodes -n0 -q` (1 passed). Potvrdil rozměry ABI a odeslání mockovaných struktur. Úplné ověření této změny patří samostatnému test kroku: `just lint`, `just typecheck` a `just pytest tests/web/test_web_screen_input.py tests/web/test_web_screen.py`. Hledání přímých volajících v celém `aifactory/tests/` našlo relevantní volání session a streamu v `test_web_screen.py`; jeho konstruktor zůstává zpětně kompatibilní. Frontend se nemění.
