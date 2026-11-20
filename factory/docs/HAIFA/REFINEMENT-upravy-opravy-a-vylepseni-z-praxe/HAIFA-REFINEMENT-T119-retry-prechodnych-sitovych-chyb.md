# T119 — Odolnost approve a auto-continue při výpadku sítě

Přechodná chyba fetch již neukončuje run, retry ani approve po jediném pokusu. Společná politika opakuje síťové chyby nejvýše pětkrát, s pauzami 10/20/40/80 sekund. Stejnou politiku používají čtení a obnovené operace PR na GitHubu, Azure DevOps a Forgejo. Rozpoznává také git/cURL low-speed timeout `Operation too slow. Less than 1 bytes/sec transferred the last 30 seconds`. Trvalé chyby autentizace, oprávnění nebo nenalezeného prostředku se neopakují.

Merge a schvalování při každém opakování znovu kontrolují stav a hlavu PR. GitHub a Forgejo při opakování vytvoření PR nejprve hledají už vytvořené otevřené PR. Azure zachovává vlastní přísné ověření vytvořeného PR proti původnímu snímku a požadovaným polím. Pokud po odeslání nelze výsledek zápisu nebo identitu schvalovatele ověřit, další pokus se zakáže. Tím se nepřevezme staré či nesouvisející PR ani znovu neodešle neověřitelný zápis. DNS selhání během navazování REST spojení je odlišeno: požadavek ještě nemohl být odeslán, proto lze approve bezpečně zopakovat. Chyby při čtení odpovědi zůstávají nejednoznačné. Komentáře zachovávají dosavadní režim jednoho odeslání a případného ověření výsledku; nejsou automaticky přehrávány. Dosavadní retry push a polling mergeability/CI zůstávají samostatnými mechanismy.

Log obsahuje `network_retry` s pořadím pokusu a délkou pauzy a závěrečný `network_result` s počtem pokusů. CLI envelope při operaci s chybou obsahuje například:

```json
{"network_operations": [{"operation": "fetch", "attempts": 2, "succeeded": true}]}
```

Tato položka patří do `data`, včetně neúspěšného envelope. Po vyčerpání fetch zůstává chyba `fetch_failed`. Operace bez chyby se do diagnostiky nepřidávají.

## Diagnostické ověření implementace

Příkaz `just pytest tests/run/test_auto_merge.py::test_chain_continues_after_transient_fetch_outage -k approve -n0` prošel: 1 test, 1 deselected. Test vkládá DNS chybu do fetch po merge prvního tasku uvnitř automatického approve, ověřuje pauzu 10 sekund pomocí simulovaných hodin, dva pokusy v diagnostice a dokončení následujícího tasku. Nepoužívá živého providera ani model.

Při opravě nálezů testovacího kroku prošel také příkaz `just pytest 'tests/providers/test_azure_write_operations.py::test_create_does_not_adopt_unverified_pr[cli-old]' -n0`: 1 test. Ověřuje, že obecný retry nepřevezme staré PR po nejednoznačném výsledku vytvoření. Regrese navíc kontrolují jediný pokus v diagnostice a ukončení bez backoffu při zakázaném opakování.

Příkazy `--help` a `--version` obcházejí import sběru síťové diagnostiky. Test startu CLI kontroluje i nepřítomnost balíčku providerů.

Při doplnění nálezů review prošel příkaz `just pytest 'tests/providers/test_azure_write_operations.py::test_approve_retries_dns_before_rest_vote[rest-1]' -n0`: 1 test. Používá skutečnou cestu `ReadClient.write` s paměťovým HTTPS spojením: první navázání vyvolá `socket.gaierror`, druhé vrátí platný hlas. Ověřuje dva pokusy, backoff, log, envelope a nepoužití CLI fallbacku. Doplněné regrese dále pokrývají vyčerpání pěti pokusů, rozlišení fáze DNS chyby a low-speed fetch.

Úplné cílené ověření opravy, lint a typecheck provádí samostatný testovací krok workflow.
