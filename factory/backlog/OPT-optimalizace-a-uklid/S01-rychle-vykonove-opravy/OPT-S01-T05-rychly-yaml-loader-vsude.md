---
id: OPT-S01-T05
title: Rychlý YAML loader všude
status: todo
depends_on: []
---

## Zadání
`yamlload.py` čte YAML přes C loader. 11 míst ale volá přímo `yaml.safe_load` (např. 5× `config/roster.py`) a C loader nepoužívá. ruamel se načítá i tam, kde se YAML jen čte.

Zadání:
1. Všechna čtení YAML mimo `yamlload.py` převést na `aifactory.yamlload`.
2. ruamel importovat až ve funkcích, které dělají úpravy se zachováním komentářů (`config/yamledit.py`, `library/config_edit.py`).

Hotovo znamená:
- Test hlídá, že mimo `yamlload.py` nikdo nevolá `yaml.safe_load` ani `yaml.load`.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
