# T119 — Opakování přechodných síťových chyb

Fetch při run, retry i approve a síťové operace providerů GitHub, Azure a Forgejo používají společnou politiku nejvýše pěti pokusů. Pauzy před dalším pokusem jsou 10, 20, 40 a 80 sekund (celkem 150 sekund plus doba samotných požadavků). Rozpočet časově omezeného probe se neprodlužuje.

Opakují se DNS chyby, přerušené spojení, timeouty (včetně git/cURL `Operation too slow`) a HTTP 5xx. Autentizace, oprávnění, HTTP 401/403/404, chybějící remote ref, konflikty a neznámé chyby končí okamžitě. Fetch zachovává kód `fetch_failed`, včetně vyčerpaného timeoutu gitu.

Retry schválení a merge probíhá na úrovni operace provideru: znovu se ověří stav PR a očekávaná hlava. GitHub a Forgejo při opakování vytvoření PR nejprve hledají existující otevřené PR stejné větve. Azure zachovává ověření proti snímku před vytvořením, času vzniku a požadovaným polím; neověřitelné vytvoření se neopakuje a nepřebírá PR podle obecného hledání větve. Komentáře se automaticky neopakují, protože nemají společný identifikátor pro bezpečné obnovení. Neověřitelný zápis Azure nastaví `ProviderError.retryable = False`, což společný retry respektuje. Platí to i pro nečitelný výsledek změny popisu a neověřený hlas schvalovatele. Výjimkou je prokazatelně neodeslaný požadavek: `socket.gaierror` při navazování spojení uvnitř REST `request()` se vrací jako běžná přechodná DNS chyba a umožňuje opakování. Stejná výjimka při čtení odpovědi či těla zůstává `WriteUncertain`.

Každá operace, která narazila na chybu, zaznamená do logu počet pokusů a výsledek. CLI vrací tyto údaje v `data.network_operations` i při chybovém envelope; běžný úspěch bez chyby strukturu envelope nerozšiřuje. Diagnostika obsahuje pouze jméno operace, počet pokusů a úspěch, bez URL a přihlašovacích údajů. Každé volání CLI má vlastní sběr diagnostiky.

Regrese bez živých služeb pokrývají přechodný úspěch, vyčerpání limitu, trvalé chyby, backoff, timeout fetch, sdílený limit vnořených operací, časový rozpočet probe, izolaci envelope, obnovu vytvoření PR a pokračování auto-continue po výpadku při run i automatickém approve.

Příkazy `--help` a `--version` neimportují retry diagnostiku ani providery, aby zachovaly lehký start CLI.
