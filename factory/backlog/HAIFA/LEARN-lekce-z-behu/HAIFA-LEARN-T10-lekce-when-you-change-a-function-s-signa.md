---
id: HAIFA-LEARN-T10
title: "Lekce: When you change a function's signature or return type, update or keep compatible e…"
status: done
archived: true
workflow: build-review
depends_on: []
related: [HAIFA-REFINEMENT-T71, HAIFA-TODO-T28]
writes: [AGENTS.md]
---

## Zadání
Zapiš do `AGENTS.md` pravidlo z lekce `L64dc3ab5`, kterou factory našla ve výtkách opakovaných v bězích:

> When you change a function's signature or return type, update or keep compatible every direct caller, including test stubs and lambdas, and run their tests.

Pravidlo formuluj stručně, zařaď ho tam, kam v souboru patří, a nenech ho odporovat tomu, co tam už je. Jiné soubory neměň.

Hotovo znamená:
- Pravidlo je v `AGENTS.md`.

### Důkazy

Výtky z 2 běhů, ze kterých lekce vznikla:

- repo `haifa`, běh `9e8e190e`, task `HAIFA-REFINEMENT-T71` (test_review): Repair aifactory/tests/run/test_followups_queue.py::test_mixed_triage_only_queues_new_agent_tasks (outside writes): make its ask_triager stub accept keyword arguments. Retain this check and report the path in needs_writes with fail_kind: needs_writes.
- repo `haifa`, běh `9e8e190e`, task `HAIFA-REFINEMENT-T71` (test_review): affected tests remain compatible: test_mixed_triage_only_queues_new_agent_tasks uses lambda *args, which rejects the newly passed task_id and binding keywords.
- repo `haifa`, běh `9e8e190e`, task `HAIFA-REFINEMENT-T71` (test_review): Repair aifactory/tests/run/test_followups_queue.py::test_mixed_triage_only_queues_new_agent_tasks so its ask_triager stub accepts keyword arguments; preserve its assertions and retain this integration check. The path is now within allowed writes.
- repo `haifa`, běh `9e8e190e`, task `HAIFA-REFINEMENT-T71` (test_review): affected tests remain compatible: test_followups_queue.py:253 still uses lambda *args, which rejects the new task_id and binding keywords and causes fallback behavior that violates the test assertions.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (review): Opravte kompatibilitu _trace_view s přímými volajícími. Zachovejte stringová varování pro Backlog a asistenta nebo je explicitně adaptujte v aifactory/src/aifactory/web/backlog.py a aifactory/src/aifactory/web/assistant.py. Nečitelná databáze musí vracet usage_error asistenta bez TypeError. Zachovejte test tests/web/test_web_assistant.py::test_invalid_repository_context_is_usage_error[trace]. Pokud helper nadále vrací strukturované objekty, doplňte regresi stringových varování nečitelné databáze do aifactory/tests/web/test_web_backlog.py. just typecheck musí projít.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (review): Opravte volajícího beforeDeadline v aifactory/web/src/views/SetupView.vue:54 nebo zachovejte kompatibilitu helperu. Timeout musí zachovat skutečný název a technický detail kroku. Zachovejte ověření v aifactory/web/src/views/SetupView.test.ts:208; test i frontendová typová kontrola musí projít.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (review): Převést všechny související stavy do EN/CS.: aifactory/web/src/lib/readiness.ts:20 již překládá stabilní identitu kroku při vykreslení. Změna beforeDeadline v aifactory/web/src/lib/machineProgress.ts:80 ale ponechala nekompatibilního volajícího v aifactory/web/src/views/SetupView.vue:54. Typová kontrola hlásí TS2322 a SetupView.test.ts:208 místo skutečného kroku dostává undefined.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (review): Backendové hlášky lokalizovat pomocí stabilních kódů a parametrů nebo explicitního locale požadavku.: aifactory/src/aifactory/web/overview.py:454,882 poskytuje kódy, parametry a původní diagnostiku. Změna _trace_view na list[JsonDict] na řádku 459 ale porušuje kontrakt přímých volajících: aifactory/src/aifactory/web/assistant.py:725 volá join nad slovníky a padá TypeError; aifactory/src/aifactory/web/backlog.py:184,195 vrací slovníky místo řetězců. Potvrzeno testem asistenta a třemi chybami mypy.

<!-- factory: lesson=L64dc3ab5 -->

## Běhy
<!-- doplňuje GIRO při schválení PR -->
