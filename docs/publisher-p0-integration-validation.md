# Publisher P0 — integração bloqueada por invariante de segurança

Validação em 2026-10-12. A integração foi preparada sem commit, partindo da base exigida. A execução funcional foi interrompida no primeiro defeito de segurança, conforme o pedido. Os resultados anteriores das entregas não foram reutilizados como aprovação desta integração.

## Bloqueio P0 comprovado

`tests/test_publisher_p0_integration.py:22` reproduz uma transferência confirmada sem persistência reconciliada, checkpoints e mutation ledger preservados, ABANDON pelo serviço real de resolução local, e um sucessor legado sobre a mesma identidade de MediaAsset. O sucessor publicado é um estado histórico simulado diretamente em SQLite; não houve upload nem publicação remota.

O guard histórico retorna `MEDIA_HISTORY_RECONCILIATION_REQUIRED`, identifica o predecessor arquivado e bloqueia uma nova reserva. Entretanto, o consumo do sucessor considera a mídia elegível, efetua delete no Fake Drive e persiste `CONSUMED`.

Observado:

```json
{"blocker": null, "category": "eligible", "fake_deletes": ["file-1"], "historical_blocker": "MEDIA_HISTORY_RECONCILIATION_REQUIRED", "media_status": "CONSUMED", "reason": null, "result": "CONSUMED"}
```

Esperado: `CONSUMED=0`, `BLOCKED=1`, nenhuma chamada delete e reserva preservada. Obtido: `CONSUMED=1`, `BLOCKED=0`. O histórico do predecessor permaneceu intacto, mas seu bloqueio foi contornado pelo consumo.

`consumption_eligibility` em `src/publisher/batch_consumption.py:52` exclui drafts ARCHIVED de sua consulta de conflitos e não consulta a proteção de histórico. `_assert_identity` revalida a mesma elegibilidade insuficiente. `cleanup_remote` também não adiciona o guard histórico. A autorização explícita de excluir foi fornecida na fixture; ela não deve substituir a reconciliação de tentativas históricas.

Nenhuma correção funcional foi aplicada. Este defeito bloqueia commit e deploy.

## Execução e isolamento

Foi executado um único teste integrado, com failfast: 1 executado, 0 aprovados, 1 falha, 0 erros. A falha é de domínio, não de harness. Zero rodadas de correção de harness.

O runner usou SQLiteIsolationGuard com allowlist de diretórios temporários e QA da worktree isolada. Uma conexão SQLite aceita e zero conexões bloqueadas; nenhuma conexão com banco operacional. Um audit hook proibiu conexões de rede fora de loopback. A fixture usa FakeIdentityDrive; uma exclusão falsa, zero escritas remotas reais.

Não foram executados testes direcionados das entregas, Chrome, suíte Publisher completa, 27 complementares, compilação Python ou node --check após o bloqueio. As fases subsequentes ficaram suspensas pelo gate de segurança. Nenhum servidor foi iniciado, nenhuma porta usada, nenhum OAuth executado.

## Integridade e alterações

Conflito único em `dashboard/publisher.html`, linhas adjacentes de dois diálogos. Mantido o batchReviewDialog da entrega B e o resolutionDialog da entrega A. Nenhuma mudança de regra de domínio para resolver conflito.

Os 19 arquivos exclusivos de uma entrega são idênticos, byte por byte no índice Git, ao respectivo commit de origem. Os três compartilhados são `dashboard/publisher.html`, `dashboard/publisher.js` e `src/publisher/service.py`: o HTML contém os dois diálogos, JS preserva as mudanças adjacentes das duas entregas e service preserva o guard e as contagens de consumo. `git diff --check` e `git diff --cached --check` passaram. Nenhum arquivo unmerged ou marcador de conflito permanece. O gate estático completo não foi executado.

As três worktrees originais permanecem limpas nos mesmos HEADs e branches. A branch operacional continua em 29d72b1459b0dd110e0cb68919b87dd795f78b63; main continua em 9f4671b28b6678da58a69bb25765314f6d6adadd. HEAD da integração continua na base. Nenhum merge, commit, push ou tag foi executado. Nenhum arquivo operacional ou Analytics integra o diff.

Arquivos adicionais às entregas: `tests/test_publisher_p0_integration.py`, reprodução da falha de segurança sem alterar testes existentes; este relatório, evidência e gate de bloqueio. A resolução HTML é a única resolução manual de código.

## Relatório solicitado

```text
PUBLISHER_ONLY_SCOPE=true

INTEGRATION_WORKTREE=C:\Users\Usuario\Documents\publisher-wt-integration
INTEGRATION_BRANCH=feature/publisher-p0-integration
BASE_HEAD=29d72b1459b0dd110e0cb68919b87dd795f78b63

MEDIA_SAFETY_COMMIT_INCLUDED=f304dfa951b265e65fba458028a66176124d9d50 (conteudo preparado; sem commit)
BATCH_CONSUMPTION_COMMIT_INCLUDED=d4aa73611cb71f6c80d25ad4386edc74e218c6f0 (conteudo preparado; sem commit)
CHERRY_PICK_CONFLICTS=1 (dashboard/publisher.html)
CONFLICT_RESOLUTIONS=batchReviewDialog de B + resolutionDialog de A
UNMERGED_FILES_REMAINING=0

ORIGINAL_WORKTREE_UNCHANGED=true
MEDIA_SAFETY_WORKTREE_UNCHANGED=true
BATCH_CONSUMPTION_WORKTREE_UNCHANGED=true

HISTORICAL_MEDIA_GUARD_VALIDATED=PARTIAL (reserva bloqueada no teste D)
ARCHIVED_HISTORY_PROTECTED=false (consumo contorna o bloqueio)
DRAFT929_EQUIVALENT_SCENARIO_BLOCKED=NOT_FULLY_VALIDATED
DRAFT52_EQUIVALENT_CANCELLATION_VALIDATED=NOT_RUN

BATCH_CONSUMPTION_VALIDATED=false
SEPARATE_DRIVE_AUTHORIZATION_VALIDATED=NOT_RUN
ABANDONED_MEDIA_PROTECTED=NOT_FULLY_VALIDATED
AMBIGUOUS_TRANSFER_PROTECTED=false (historico nao reconciliado consumido)
PARTIAL_RESULTS_VALIDATED=NOT_RUN
IDEMPOTENCY_VALIDATED=NOT_RUN

CROSS_FEATURE_SECURITY_TESTS_PASSED=false
CROSS_FEATURE_DEFECTS_FOUND=1

TARGETED_TESTS_PASSED=0
TARGETED_TESTS_FAILED=1
TARGETED_TESTS_ERRORS=0

PUBLISHER_CORE_DISCOVERED=NOT_RUN
PUBLISHER_CORE_PASSED=0
PUBLISHER_CORE_SKIPPED=0
PUBLISHER_CORE_FAILED=NOT_RUN
PUBLISHER_CORE_ERRORS=NOT_RUN

COMPLEMENTARY_TESTS_PASSED=0
COMPLEMENTARY_TEST_COUNT=NOT_RUN (27 previstos)

STATIC_CHECKS_PASSED=false (incompletos; diff checks passaram)
INTEGRATED_DIFF_AUDITED=true

OPERATIONAL_DB_WRITES=0
TIKTOK_REMOTE_ACTIONS=0
DRIVE_REMOTE_WRITES=0
REAL_DRIVE_DELETIONS=0
ANALYTICS_CODE_CHANGED=false

CODE_INTEGRATION_ONLY=true
COMMIT_CREATED=false
OPERATIONAL_BRANCH_UNCHANGED=true
MAIN_UNCHANGED=true
REMOTE_PUSH_PERFORMED=false

INTEGRATION_VALIDATION_PASSED=false
READY_FOR_FINAL_INTEGRATION_COMMIT=false
READY_FOR_OPERATIONAL_DEPLOYMENT=false
```

## Conteúdo preservado no índice, sem aprovação para commit

- `dashboard/publisher.html`
- `dashboard/publisher.js`
- `docs/publisher-batch-consumption.md`
- `docs/publisher-media-history-safety.md`
- `docs/publisher-p0-integration-validation.md`
- `src/publisher/account_safety.py`
- `src/publisher/batch_consumption.py`
- `src/publisher/http.py`
- `src/publisher/library.py`
- `src/publisher/lifecycle.py`
- `src/publisher/manual_resolution.py`
- `src/publisher/media.py`
- `src/publisher/media_history.py`
- `src/publisher/posting_review.py`
- `src/publisher/queue_policy.py`
- `src/publisher/service.py`
- `src/publisher/worker.py`
- `tests/sqlite_isolation_harness.py`
- `tests/test_publisher_batch_consumption.py`
- `tests/test_publisher_batch_consumption_browser.py`
- `tests/test_publisher_manual_resolution.py`
- `tests/test_publisher_media_history_safety.py`
- `tests/test_publisher_p0_integration.py`
- `tests/test_sqlite_isolation_harness.py`
