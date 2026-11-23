# HAIFA-S13-T04 — Tajná vzdálená plocha

Dashboard rozlišuje dvě čtyřstiskové sekvence: Control, Shift, Control, Shift pro terminál a Shift, Control, Shift, Control pro plochu. Opakování kláves ignoruje, po prodlevě přes jednu sekundu rozpracovanou sekvenci zahodí. Rozpoznání resetuje oba směry. Otevřený overlay dovoluje pouze svou vlastní přepínací sekvenci; posluchač dashboardu používá capture fázi. V běžném rozhraní nepřibylo žádné ovládání ani odkaz.

Plocha se načítá líně a používá existující API dostupnosti, binárního streamu a vstupu. Parser skládá big-endian délku i JPEG přes hranice chunků, kontroluje neúplné rámce a odmítá nulové délky nebo délky nad 64 MiB. Stream se ruší při zavření, odpojení komponenty a pagehide.

Canvas zachovává poměr stran. Dekódování zahazuje opožděné výsledky, nevykreslené bitmapy nahrazuje nejnovějšími a všechny bitmapy uvolňuje. Vykreslování i slučování pohybů používá requestAnimationFrame. Souřadnice mimo obraz nepředává; kolečko převádí na jednotky API. Vstupní dávky mají nejvýše 200 událostí a odesílají se sériově. Blur a zavření zahodí čekající vstup a za probíhajícím požadavkem odešlou release_all.

Kolečko v API nemá vlastní souřadnice, proto před ním fronta zachovává poziční move. Další pohyby tuto pozici neodstraňují ani před vykreslením, ani při čekání na probíhající požadavek. Slučují se pouze pohyby, které nejsou nutné pro umístění kolečka. Regrese ověřuje dvě kolečka na různých místech následovaná dalšími pohyby v obou situacích. Další test předává 450 neslučitelných vstupů a ověřuje jejich úplné pořadí, dávky 200/200/50 a nejvýše jeden současný požadavek.

Klávesnice předává fyzické code a down a blokuje výchozí reakce prohlížeče tam, kde to prohlížeč dovolí. Stavové zprávy vznikají ze stabilních stavů a důvodů, s EN/CS katalogem vyhodnocovaným při renderování. Zákaz mimo loopback sdílí text s terminálem; chybějící oprávnění obsahuje odkaz na dokumentaci macOS. Ukončený nebo selhaný přenos nabízí obnovení.

Regresní testy pokrývají obě sekvence a jejich překryv, časování a repeat, fragmentaci streamu, ochranu délky rámce, rušení čtení, letterbox, myš, serializaci vstupů, klávesnici, cleanup, životní cyklus bitmap, lokalizaci hlášek a vzájemné vyloučení overlayů v aplikaci.

Testovací běh 07_* před touto opravou potvrdil úspěšný lint, typovou kontrolu, build včetně vue-tsc --noEmit, 1938 frontendových testů a tři browserové regrese terminálu. Používá nativní Git sh pomocí `just --shell sh --shell-arg=-cu`, například s receptem web-test; stejné přepnutí platí pro ostatní recepty. Ověření opravy pořadí kolečka a nových regresí i přegenerování distribučního buildu provádí samostatný testovací krok workflow na konečném kódu.
