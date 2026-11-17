---
id: OPT
title: Optimalizace a úklid
archived: true
workflow: build-test-review
writes: [aifactory/, justfile]
auto_continue: true
---

Zrychlit GIRO hlavně na Windows, kde Defender kontroluje každé spuštění procesu a otevření souboru, a uklidit strukturu kódu, která vznikla evolučně. Podkladem je audit z 16. 11. 2026. Čísla řádků v zadáních platí k tomuto datu, po dřívějších úkolech se posunou.

## Společná kritéria

- Měřítkem výkonových úkolů je počet spuštěných procesů a otevření souborů na operaci, ne čas na macOS. Kde to jde, test hlídá počet procesů gitu na operaci (patch git runneru nebo `GIT_TRACE2_EVENT`) a PR uvádí počet před a po.
- Chování pro uživatele se nemění, pokud zadání neříká jinak.
- Kód musí fungovat s gitem 2.37 (vývojový Mac). Novější funkce gitu jen s ověřením verze a záložní cestou.
- Kontroly podle AGENTS.md: vždy `just lint` a `just typecheck`, cílené testy změněného chování a přímých volajících vyhledaných v celém `aifactory/tests/`, každý upravený testový soubor. Při změně frontendu `just web-test` a `just web-build`, browserové testy jen dotčených obrazovek.
- Přesuny a dělení modulů nenechávají kompatibilní shimy pro interní importy. Volající se přepíšou.
