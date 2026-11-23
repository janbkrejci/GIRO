# HAIFA-S13-T03: Implementace vstupu macOS a X11

Tajná plocha nyní obsahuje vstupní backendy pro macOS a Linux s X11 podle
[specifikace](../../../specs/HAIFA/S13-tajna-vzdalena-plocha/HAIFA-S13-T03-tajna-plocha-vstup-na-macos-cgevent-a-li.md).
Výběr platformy i HTTP kontrakt z T02 zůstávají kompatibilní.

macOS načítá ApplicationServices a CoreFoundation přes ctypes. Převod z
normalizovaných souřadnic používá body hlavního displeje z CGDisplayBounds,
takže nezdvojuje pozice podle Retina pixelů. Myš podporuje tažení všemi třemi
tlačítky, kolečko obě osy a klávesnice virtuální keycody včetně příznaků
modifikátorů pro Cmd+C. Vytvořené události se uvolňují přes CFRelease.
Chybějící oprávnění Zpřístupnění vrací důvod permission_denied a HTTP 501
s návodem na příslušné nastavení macOS.

X11 používá vlastní spojení s displejem a ověřuje rozšíření XTest.
Pohyb vychází z geometrie zachytávání, kolečko používá tlačítka 4–7
a klávesnice keysymy převáděné na keycody serveru. Zlomky scrollu se
akumulují. Každá operace provádí XFlush, čímž je odeslána i poslední operace
dávky. release_all uvolní držené vstupy a zachová spojení pro další dávku;
zánik backendu spojení uzavře.

Backendy evidují držené vstupy také pro přímé použití a po neúspěšném
uvolnění je zachovají pro opakovaný pokus. Sdílený InputState T02 zůstává
zodpovědný za serializaci a úklid při odpojení. Import backendů žádnou
nativní knihovnu neotevírá a nepřibývají závislosti.

Regresní testy mockují ctypes a neodesílají události na skutečnou plochu.
Builder neprováděl testovací kontroly; podle workflow patří samostatnému
testovacímu kroku. Doporučené příkazy z kořene repozitáře:

```powershell
just lint
just typecheck
just pytest tests/web/test_web_screen_input_platforms.py tests/web/test_web_screen_input.py
```

Hledání volajících v celém aifactory/tests/ našlo testy kontraktu vstupu
v test_web_screen_input.py; nové backendy nemění capture ani stream.
Nativní ovládání macOS a X11 nebylo na Windows ručně ověřeno.
