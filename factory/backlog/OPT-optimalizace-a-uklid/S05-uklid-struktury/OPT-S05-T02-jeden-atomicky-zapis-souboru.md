---
id: OPT-S05-T02
title: Jeden atomický zápis souboru
status: todo
depends_on: [OPT-S05-T01]
---

## Zadání
Zápis přes dočasný soubor a `os.replace` existuje v 15 kopiích: `backlog/edit.py:204`, `web/settings.py:300`, `web/registry.py:260`, `repo_settings.py:215`, `migration_guard.py:221`, `config/roster.py:202`, `library/share.py:128`, `library/history.py:105`, `library/config_edit.py:1018`, `run/mainwrites.py:102`, `harness/settings.py:83`, `harness/probe.py:161`, `testing/profile.py:152` a další. Na Windows `os.replace` selže s `PermissionError`, když soubor drží jiný proces (i Defender), a každá kopie to řeší jinak nebo vůbec.

Zadání:
1. `oscompat.atomic_write(path, data)` pro text i bytes s krátkým opakováním při `PermissionError` na Windows.
2. Nahradit všechny kopie.

Hotovo znamená:
- Test selhání: simulovaný `PermissionError` se zopakuje, po vyčerpání pokusů vyhodí chybu, cílový soubor zůstane nepoškozený a dočasný soubor se uklidí.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
