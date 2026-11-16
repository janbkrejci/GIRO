---
id: OPT-S02-T04
title: Konfigurace běhu načtená jednou
status: todo
depends_on: [OPT-S02-T02]
---

## Zadání
`load_run_config` (`config/run.py:76-83`) stojí 3–7 procesů gitu (`rev-parse --show-toplevel`, `rev-parse --verify`, `ls-tree`, `cat-file --batch`, `diff-index`, `hash-object`, `ls-files`) a nemá cache. Volá se z 27 míst, některá ji v jedné operaci načítají opakovaně: `run/allow_writes.py:304,310,330`, `workflow/adaptive.py:163,263`, `run/queue.py:887` `_max_parallel`. `gitops.main_root` (`run/gitops.py:65`) se volá vedle ní bez cache.

Zadání:
1. Načtenou konfiguraci předávat dolů místo opětovného načtení.
2. `config_changes` (stav necommitnutých změn konfigurace) počítat jen tam, kde se zobrazují jeho varování.
3. Toplevel a sha base z cache z S02-T02.

Hotovo znamená:
- Test ověří, že jedno naplánování fronty a jedna kontrola allow_writes načtou konfiguraci jednou.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
