# Příloha pro autory workflow

Workflow YAML obsahuje jméno, popis, kroky a `accept`. Patří do `.factory/workflows/<jméno>.yaml` nebo knihovny; ověř: `giro workflow check .factory/workflows/<jméno>.yaml`.

## Kroky

**Kroky agentů**: `plan`, `scout`, `build`, `fix`, `revise`, `ask`, `resolve`, `test_plan`, `review`, `document`. Volby: `agent`, `harness`, `model`, `thinking`, `when`, `id`, `description`, `input`.

**Kódové kroky**: `test` (vykoná plán testera), `commit`, `changes`, `command` (`id`, `argv`, `timeout`), `rebase`, `rebuild`.

Tester obnovuje lokální profil příkazů, výběru a časů při změně konfigurace. Selhání opravuje builder, chybějící testy kontroluje reviewer. Automatická revize ani triáž neběží; `test_review` je explicitní.

`test_plan.coverage`: `full` žádá celou sadu, `scoped` kontroly změny; jiný odmítne.

`plan_keeps_checks`: selhanou kontrolu neodstraňuj. Oprava `argv` vyžaduje stejné
jméno v `checks` a záznam `dropped` se stejným `name` a důvodem změny příkazu
v `reason`. Výjimka: zúžení selhané sady `full` při požadovaném `scoped`
zachovává jméno, ale nepotřebuje `dropped`.

## Podmínky a smyčky

- `repeat: {max: N, until: podmínka}` s vnořenými `steps` opakuje kroky; `until` se vyhodnotí po každém kroku.
- `when: podmínka` krok přeskočí, když neplatí.
- `accept: podmínka` rozhodne, zda běh uspěl.

Podmínky: výstupy (`test.passed`, `test.coverage`, `review.approved`, `rebase.conflict`), `and`, `or`, `not`, porovnání. Nevykonávají kód.

```yaml
name: build-test-review
description: Build, test with repairs, review with revision
steps:
  - build
  - test_plan
  - repeat: {max: 3, until: test.passed}
    steps:
      - test
      - fix
      - test_plan
  - repeat: {max: 2, until: review.approved}
    steps:
      - review:
          input: [build, fix, revise]
      - revise
      - test_plan:
          id: replan
          input: [review]
      - test:
          id: retest
  - commit:
      when: test.passed and review.approved
accept: test.passed and review.approved
```

Auto-merge mergne jen PR workflow, které má krok `review`.

## roles.yaml

`.factory/roles.yaml` mění agenty kroků. Uváděj jen změněné role; úplná kopie registru (`roles_full_copy`) blokuje budoucí aktualizace.
