# Publisher — gate completo final do hardening P0

**Gate técnico aprovado. Conteúdo apto para commit de segurança; deploy operacional não aprovado. Nenhum commit foi criado.**

Worktree exclusiva `C:/Users/Usuario/Documents/publisher-wt-integration`, branch `feature/publisher-p0-integration`, HEAD `ff539f330c4121f7f91a196867c3f04607cac027`. Main protegida `9f4671b28b6678da58a69bb25765314f6d6adadd`. Referência: `docs/publisher-persistence-consolidation.md`.

## Resultados

| Gate | Resultado |
|---|---|
| A — Publisher completo | **839 executados: 837 aprovados, 2 skips conhecidos, zero failures/errors**. Tempo: 2219,914 segundos. Processo terminou com exit code 0. |
| B — complementares | **27/27 aprovados**, zero failures/errors/skips; exit code 0. |
| C — segurança e recuperação | Os **70 P0 e 10 testes de prova confiável** passaram dentro da descoberta integral. Cobertura obrigatória conferida caso a caso. |
| D — Chrome | **Cinco smokes anteriores reutilizados**, zero reexecuções: arquivos preparados, contratos e bytecode do harness executado permanecem idênticos. |
| E — integridade | **29 hashes preparados preservados**, compile de 23 Python, três checks JavaScript, diff/cached checks, staging vazio, ausência de conflitos e worktrees/main/HEAD preservados. |

A seleção de A reproduz integralmente `python -m unittest discover -s tests -p "test_publisher*.py"`: mesmo diretório/padrão, top-level padrão e classes importadas mantidas. O runner adiciona apenas isolamento e registro. A referência de 829 passou a 839 pelos dez testes novos do protocolo de prova. Não houve filtragem para remover casos problemáticos, fail-fast de erros comuns, mudança de produção, alteração de teste/fixture ou rodada de estabilização nesta validação.

Os dois skips são exclusivamente os opt-ins operacionais conhecidos: `QueueBrowserTests.test_operational_snapshot_read_only` e `OperationalThumbnailTests.test_four_real_items_in_temporary_chrome`. Nenhum deles acessou o SQLite operacional ou o Drive real. Os testes browser sintéticos integrantes da suíte permaneceram habilitados.

`UNEXPECTED_FIXTURE_ERRORS=0`. Uma exceção **deliberada e esperada** foi registrada pelo teste `HeadlineFixtureCleanupTests.test_handler_failure_still_closes_sqlite_and_removes_directory`: `ValueError("Fixture handler failure")`. A classificação exige identidade exata do teste, módulos/classes de server e handler, flag de injeção e tipo/mensagem da exceção. Ela comprova cleanup após falha simulada; não é um erro inesperado, nem foi ocultada. O registro bruto permanece no JSON/log de A.

## Cobertura de segurança confirmada

- Limpeza automática rejeitada nas rotas, enqueue, dispatcher e retomada de execuções legadas. Publicação declarada, ocultação e Save observado não emitem consentimento de exclusão.
- Consumo individual e bulk exigem autorização independente, vinculada à identidade e quantidade; histórico incerto bloqueia antes de lookup/delete. Locks, idempotência, concorrência, confirmação remota e recuperação de checkpoints passaram.
- Recibo Fake confiável aceito somente no SQLite temporário exato da fixture. Conta/Draft/mídia/operação diferentes, ID genérico, ambiguidade, adulteração, expiração/futuro e replay entre Drafts ou operações posteriores foram rejeitados.
- Promoção atômica com auditoria passou: validação sob write lock, rollback integral quando a auditoria falha, concorrência idempotente e reabertura de conexão. Legenda, URL genérica, screenshot e transferência isolada não promovem persistência.
- A função de autoridade de produção retorna um dicionário vazio. Não há import do emissor Fake pelo código de produção, chave de teste configurada ou endpoint para estabelecer confiança artificial. A chave aleatória da fixture permanece no código de teste/processo temporário, com escopo de conexão exato.
- UNCONFIRMED não autoriza reupload/retomada cega. Encerramento administrativo preserva evidências e restrições históricas, não confirma persistência nem concede consumo. Só a tarefa selecionada fecha; a execução permanece pausada se houver outro item pendente. Falha comprovadamente local mantém recuperação/cancelamento seguro.
- Os cinco smokes reutilizados comprovam a distinção visual entre transferência confirmada e persistência incerta, promoção supervisionada de um Draft novo por prova Fake, cancelamento local, UNKNOWN/encerramento e consumo parcial com origem incerta preservada. Os registros positivos são produzidos pelo protocolo, sem semear PERSISTED para substituir a promoção.

Os IDs de cada caso aprovado estão em `gate-C-security-coverage.json`; a auditoria está em `integrated-diff-audit.json`. A suficiência do Fake verifica o contrato e o domínio, sem demonstrar uma capacidade do Studio real.

## Preflight, isolamento e integridade

O preflight confirmou os 29 arquivos preparados contra os hashes do último gate direcionado aprovado: nenhum conteúdo posterior não validado. Registrou 21 arquivos unstaged, oito untracked preparados, zero staged e zero conflitos. O diff acumulado foi preservado em `accumulated-tracked.patch`, acompanhado das cópias baseline dos arquivos novos e existentes. A revisão permaneceu restrita ao Publisher, seus testes, UI e documentação.

As duas execuções usaram **1322 conexões SQLite de fixtures/temporárias ou em memória; zero conexões operacionais, zero conexões bloqueadas, zero violações de isolamento e zero requisições de página externas**. A porta operacional 8765 foi proibida; os servidores e perfis Chrome foram isolados. Não se exigiu nem se leu hash global do SQLite operacional. Drafts #44/#49/#51/#52, MediaAssets #924/#929/#926 e seus registros operacionais não foram acessados pelos testes.

Nenhuma chave, credencial ou artifact operacional foi alterado. Os arquivos preparados permaneceram byte a byte iguais, incluindo testes e fixtures. A única edição do repositório realizada por este gate é este relatório; runner/evidências ficam na área QA ignorada. Python compile altera somente seus caches locais ignorados. Não houve upload, Save Draft, publicação, OAuth, escrita/exclusão Drive real, restart operacional, Analytics, staging, commit, merge, push ou tag.

Worktrees protegidas permanecem como no preflight: operacional `29d72b1459b0dd110e0cb68919b87dd795f78b63`, media-safety `f304dfa951b265e65fba458028a66176124d9d50`, batch-consumption `d4aa73611cb71f6c80d25ad4386edc74e218c6f0`; todas com o mesmo estado tracked. Main e HEAD da integração permanecem iguais.

## Limite operacional

`READY_FOR_SECURITY_COMMIT=true` aprova o registro do conteúdo tecnicamente validado em uma etapa separada; não executa esse commit.

`READY_FOR_OPERATIONAL_DEPLOYMENT=false`: o coletor real implementado continua sem emissor de prova independente inequívoca. O Studio real não foi acessado neste gate. Drafts reais sem prova suficiente permanecem UNCONFIRMED; histórico remoto incerto continua bloqueando reutilização, retry e consumo. Uma execução verde não autoriza alteração operacional, cleanup real ou confirmação artificial. Deploy requer decisão separada considerando essas limitações.

## Relatório obrigatório

```text
PUBLISHER_ONLY_SCOPE=true
CURRENT_BRANCH=feature/publisher-p0-integration
CURRENT_HEAD=ff539f330c4121f7f91a196867c3f04607cac027
PREPARED_SOURCE_HASHES_PRESERVED=true
PUBLISHER_CORE_DISCOVERED=839
PUBLISHER_CORE_PASSED=837
PUBLISHER_CORE_SKIPPED=2
PUBLISHER_CORE_FAILED=0
PUBLISHER_CORE_ERRORS=0
UNEXPECTED_FIXTURE_ERRORS=0
COMPLEMENTARY_TESTS_DISCOVERED=27
COMPLEMENTARY_TESTS_PASSED=27
COMPLEMENTARY_TESTS_FAILED=0
COMPLEMENTARY_TESTS_ERRORS=0
P0_SECURITY_REGRESSIONS_PRESERVED=true
TRUSTED_FAKE_PROOF_VALIDATED=true
INVALID_PROOF_REJECTED=true
ATOMIC_PERSISTENCE_PROMOTION_VALIDATED=true
PRODUCTION_HAS_NO_FAKE_TRUST_AUTHORITY=true
AUTO_CLEANUP_BYPASS_BLOCKED=true
HISTORICAL_MEDIA_GUARD_PRESERVED=true
BLIND_REUPLOAD_BLOCKED=true
ADMINISTRATIVE_CLOSURE_SAFE=true
BATCH_CONSUMPTION_PRESERVED=true
CHROME_SMOKE_RESULTS_REUSED=true
CHROME_SMOKES_REEXECUTED=0
HARNESS_FIXES_APPLIED=0
REAL_PRODUCT_DEFECTS_FOUND=0
COMPILE_CHECK_PASSED=true
JS_SYNTAX_CHECK_PASSED=true
GIT_DIFF_CHECK_PASSED=true
INTEGRATED_DIFF_AUDITED=true
REAL_STUDIO_UNIQUE_PROOF_AVAILABLE=false
UNVERIFIED_REAL_DRAFT_REMAINS_UNCONFIRMED=true
OPERATIONAL_DB_WRITES=0
TIKTOK_REMOTE_ACTIONS=0
DRIVE_REMOTE_WRITES=0
REAL_DRIVE_DELETIONS=0
ANALYTICS_CODE_CHANGED=false
COMMIT_CREATED=false
MAIN_UNCHANGED=true
MERGE_PERFORMED=false
REMOTE_PUSH_PERFORMED=false
FULL_SECURITY_GATE_PASSED=true
READY_FOR_SECURITY_COMMIT=true
READY_FOR_OPERATIONAL_DEPLOYMENT=false
REMAINING_OPERATIONAL_LIMITATIONS=REAL_STUDIO_TRUSTED_PROOF_ISSUER_UNAVAILABLE;UNVERIFIED_DRAFTS_REMAIN_UNCONFIRMED;UNCERTAIN_HISTORY_BLOCKS_REUPLOAD_AND_CONSUMPTION;OPERATIONAL_DEPLOYMENT_REQUIRES_SEPARATE_DECISION
```

Evidências completas: `data/publisher/qa/full-hardening-final/` — preflight/baseline, logs e JSONs integrais, cobertura, reutilização Chrome, auditoria do diff, postflight e relatório estruturado. Relatórios anteriores e resultados brutos foram preservados.
