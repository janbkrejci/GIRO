# HAIFA-S13-T02: API vstupu tajné plochy

`POST /api/screen/input` je dostupné pouze v globálním dashboardu. Vyžaduje loopback server i klienta a běžný write guard (Origin, Sec-Fetch-Site, JSON). Tělo má přesně tvar `{ "events": [...] }`, nejvýš 200 událostí. Celá dávka se validuje před prvním vstupem.

Podporované události:

- `{"type":"move","x":0.5,"y":0.5}`
- `{"type":"button","button":"left","down":true,"x":0.5,"y":0.5}`; také `middle` a `right`. Nejprve se přesune kurzor.
- `{"type":"wheel","dx":0,"dy":1}`; jednotka je zářez kolečka (120 Windows jednotek), kladně doprava/nahoru. Zlomky jsou povoleny; každý směr má absolutní mez `(2^31-1)/120`.
- `{"type":"key","code":"KeyA","down":true}`
- `{"type":"release_all"}`

Souřadnice jsou konečná čísla 0–1 včetně krajů vůči primárnímu monitoru, nikoli vůči zmenšenému snímku. Booleany se za souřadnice nepovažují. `down` musí být boolean. Přebytečná nebo chybějící pole jsou neplatná.

Úspěch používá standardní JSON obálku s `data: {"applied": 1, "ignored": []}`. `applied` počítá přijaté události, nikoli počet volání SendInput. `ignored` obsahuje neznámé hodnoty `code` v pořadí příjmu; jejich události se neprovedou. Prázdná dávka je povolena.

Chyby: 400 `usage_error` pro neplatnou dávku, 403 `screen_disabled` mimo loopback, 501 `screen_unsupported` pro nepodporovaný backend, 502 `screen_input_failed` při selhání SendInput. Write guard může vrátit 403 `cross_origin` nebo 415 `unsupported_media_type`. Selhání backendu může nastat po provedení části platné dávky.

Tabulka fyzických `KeyboardEvent.code` zahrnuje A–Z, 0–9, F1–F12, navigaci, běžné řídicí klávesy, levé i pravé modifikátory (Meta i OS aliasy), US interpunkci a numerickou klávesnici. Windows posílá scancody; výsledný znak závisí na rozložení klávesnice stroje dashboardu. IME ani schránka nejsou součástí API.

Backendy mají konstruktor `InputBackend(geometry)` a metody `move`, `button`, `wheel`, `key`, `release_all`. Sdílený `InputState` serializuje dávky i cleanup, drží pouze aktuálně stisknuté vstupy a před backendovým hookem `release_all` odešle jejich uvolnění. Ukončení streamu i zavření aktivní session při shutdownu volá stejný cleanup. Windows používá ctypes načítané až při konstrukci backendu, bez nové závislosti. Virtuální plocha zahrnuje i záporné počátky monitorů; normalizace používá první a poslední pixel.

`GIRO_SCREEN_BACKEND=fake` zapíná výhradně paměťový záznam pro testy. Produkční backend vstup neloguje ani neukládá. Pro macOS a Linux se vybírají moduly `screen_input_macos` a `screen_input_x11`; jejich chybějící import znamená nepodporovanou platformu. Implementace těchto modulů patří T03, frontend T04.
