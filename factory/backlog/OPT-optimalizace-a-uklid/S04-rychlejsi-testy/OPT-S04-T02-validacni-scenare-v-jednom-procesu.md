---
id: OPT-S04-T02
title: Validační scénáře v jednom procesu
status: todo
depends_on: []
---

## Zadání
`validation/runner.py:162` a `validation/context.py:213` spouštějí pro každý krok scénáře `python -m validation.worker`. Každý krok tak znovu importuje CLI (1,3 s a víc). `web/test_web_task_run.py:37` používá stejný vzor. Pět z 20 nejpomalejších testů jsou validační scénáře, nejpomalejší 125 s.

Zadání:
1. Pro scénáře s falešným harnessem runner volá `validation.worker.main` ve stejném procesu s izolovaným prostředím (env, cwd, `GIRO_HOME`) a po kroku ho obnoví.
2. Jeden scénář zůstane s podprocesem jako smoke test. Živé validace (`just validate`) mohou podprocesy používat dál.
3. Stejně upravit `web/test_web_task_run.py`, pokud nejde o test samotného spuštění procesu.

Hotovo znamená:
- Testy validace projdou a PR uvádí čas `tests/validation` před a po.

## Běhy
<!-- doplňuje HAIFA při schválení PR -->
