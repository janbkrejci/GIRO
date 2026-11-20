# T120 — Projektový Kanban a detail běhu

Kliknutí nebo Enter na názvu či kódu projektu na Přehledu otevře jeho Kanban ve správném repozitáři. Platí to i po předchozím použití Stromu. Projektový filtr funguje v obou režimech; režim lze dál přepínat klávesnicí. Běžný vstup do Backlogu obnovuje uloženou volbu zobrazení.

Běžící karta vede přímo na běžící pokus, selhaná na poslední odpovídající selhání včetně abortovaného běhu. Starší selhání neodvede uživatele od nového běžícího pokusu. Dokud nejsou dostupná data s ID odpovídajícího běhu, karta vede na detail tasku. Stejně se chovají ostatní karty. Tlačítka karet, archivace a přesouvání zůstávají dostupné.

Regrese pokrývají projektový odkaz přes oba uložené režimy, spotřebování odkazu, filtr a izolaci repozitářů, obnovení běžného Backlogu, přímé odkazy na běhy, abortování, blokování, opožděné odpovědi a fallback. Browser scénář ověřuje Enter na projektovém odkazu, výchozí Kanban, filtr a přepnutí na Strom klávesnicí.
