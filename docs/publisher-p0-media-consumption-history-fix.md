# Publisher P0 — proteção histórica obrigatória no consumo

Correção funcional validada em 2026-10-12, exclusivamente na worktree de integração. O P0 original passou: a mídia com transferência histórica não reconciliada retorna `BLOCKED`, com `MEDIA_HISTORY_RECONCILIATION_REQUIRED`, zero exclusões no Fake Drive e estado `RESERVED` preservado. A validação completa da integração permanece pendente; esta entrega está pronta para esse gate separado, sem commit ou deploy.

## Diagnóstico e contrato corrigido

Fluxo auditado: UI → endpoint → elegibilidade/claim por item → identidade e histórico → checkpoint de cleanup → adapter Drive → confirmação de ausência → `CONSUMED` → limpeza local e auditoria.

A falha original estava em duas fronteiras: `consumption_eligibility` ignorava predecessores ARCHIVED em sua consulta de conflito e não consultava a fonte canônica de histórico; `cleanup_remote`, compartilhado com consumo individual e recuperação do worker, também não consultava essa fonte. A declaração local de publicação e a autorização de excluir não reconciliam tentativas antigas.

A fonte canônica continua em `media_history.py`. O contexto `consume` exige vínculo explícito de Draft e conta, preserva os bloqueios de identidade inconclusiva, evidências conflitantes, execução antiga sem prova local e efeito remoto não reconciliado, incluindo registros arquivados. Histórico reconciliado pode prosseguir no consumo permitido pelo estado atual. As regras de reserva e novo upload continuam bloqueando reutilização; consumo não concede autorização de reupload.

`assert_cleanup_allowed` concentra as validações existentes do lifecycle e o guard histórico. O pipeline o consulta antes de qualquer adapter, antes do checkpoint e novamente sob `BEGIN IMMEDIATE` antes do efeito remoto/reconciliação de ausência. As verificações posteriores continuam vinculadas à identidade e conta inicialmente aprovadas. Bloqueios históricos propagam código explícito, sem marcar consumo, liberar reserva ou reescrever evidência antiga. Itens interrompidos continuam pendentes, sem retry, e agora exibem o motivo histórico.

O endpoint individual faz preflight antes de enfileirar. Lote, worker, auto-cleanup, cleanup reconciliado e recuperação usam o mesmo gate compartilhado. A exclusão direta da Biblioteca também respeita o blocker canônico e mantém a validação e a chamada remota no mesmo lock, fechando a rota alternativa após archive.

O construtor real do adapter já faz autenticação e consultas de metadata. Por isso os caminhos de produção agora passam factories: a construção ocorre somente depois do guard dentro da transação existente. Mídia bloqueada não chega sequer à construção; recovery de uma mídia já consumida não cria adapter. O batch mantém a autorização destrutiva separada e os resultados por item.

## Atomicidade e regressões

O cleanup existente já mantém o lock IMMEDIATE durante o efeito remoto. Os claims de lote serializam solicitações, mas não constituem uma proteção global contra alterações de histórico por outros serviços. A correção reutiliza o protocolo existente, sem criar tabelas, migrations ou um novo mecanismo de retry. A rota da Biblioteca, que não possuía claim seguro, passa a usar o mesmo tipo de lock.

Regressões cobrem histórico após preflight e após o checkpoint, troca de identidade entre avaliações, writer concorrente impedido durante construção/delete, confirmações HTTP individual/bulk e Biblioteca, ausência remota com histórico incerto, recovery local bloqueado, reserva legada sem reupload/cancelamento indevido, reconciliação legítima, cancelamento local seguro, idempotência e solicitações concorrentes.

No lote misto, uma mídia historicamente bloqueada e uma abandonada retornam BLOCKED, duas elegíveis são consumidas com autorização e uma já consumida retorna ALREADY_CONSUMED. Resultado PARTIAL, motivos por mídia e nenhuma exclusão dos itens protegidos. A repetição não duplica chamadas.

## Validação da versão final

**217 execuções aprovadas, 0 falhas, 0 erros, 0 skips; 194 IDs de teste distintos.** O loader repete 23 casos importados de `PublisherRemoteTests`; eles estão incluídos no total executado, sem inflar a contagem de testes distintos. O arquivo integrado contém 23 casos, incluindo o P0 original e 22 casos adicionais. Nenhuma asserção de segurança original foi removida.

| Conjunto afetado | Casos próprios distintos |
|---|---:|
| test_publisher_p0_integration | 23 |
| test_publisher_media_history_safety | 28 |
| test_publisher_batch_consumption | 25 |
| test_publisher_persistence | 32 |
| test_publisher_remote | 23 |
| test_publisher_media_library | 21 |
| test_publisher_headline_preparation | 22 |
| test_publisher_queue_management | 11 |
| test_publisher_manual_resolution | 9 |
| **Total distinto** | **194** |

Runner com SQLiteIsolationGuard, raízes temporárias explícitas e bloqueio de conexões externas a loopback: **352 conexões SQLite temporárias aceitas, zero fora da allowlist e zero tentativas de conexão remota bloqueadas**. Todos os efeitos Drive/TikTok das fixtures usam fakes. A execução final terminou com exit code 0 em 917,000 segundos. A rodada preliminar anterior não foi usada como aprovação da versão final.

Resultado observado no P0:

```json
{"blocker":"MEDIA_HISTORY_RECONCILIATION_REQUIRED","category":"blocked","fake_deletes":[],"historical_blocker":"MEDIA_HISTORY_RECONCILIATION_REQUIRED","media_status":"RESERVED","reason":"MEDIA_HISTORY_RECONCILIATION_REQUIRED","result":"BLOCKED"}
```

Compilação Python de `src/publisher` e do teste integrado, `node --check dashboard/publisher.js`, `git diff --check` e `git diff --cached --check` aprovados. A suíte Publisher completa, os 27 complementares e Analytics não foram executados nesta tarefa.

Evidência local ignorada pelo Git: [log da versão final](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/p0-fix-final-targeted.log), [resumo JSON](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/p0-fix-final-result.json), [runner isolado](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/run_p0_fix_targeted.py) e [delta funcional](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/p0-fix-functional.patch).

## Arquivos alterados nesta tarefa

As alterações integradas anteriores foram preservadas. Os hashes do HTML com o conflito resolvido e do JavaScript são idênticos aos do início desta tarefa. Todos os arquivos previamente existentes, exceto os sete abaixo, mantêm seu conteúdo inicial. Este relatório é o oitavo arquivo.

| Arquivo | Justificativa |
|---|---|
| src/publisher/media_history.py | Contexto canônico de consumo sem conceder reutilização/upload. |
| src/publisher/lifecycle.py | Guard compartilhado, revalidação nos locks e criação tardia do adapter. |
| src/publisher/batch_consumption.py | Elegibilidade histórica, motivo por item, recuperação pendente e factory protegida. |
| src/publisher/http.py | Preflight individual e factory da Biblioteca após guard. |
| src/publisher/service.py | Bloqueio histórico e lock na exclusão direta da Biblioteca. |
| src/publisher/worker.py | Todos os caminhos de cleanup usam factory tardia no pipeline comum. |
| tests/test_publisher_p0_integration.py | Preserva e reforça o P0 original; acrescenta 22 regressões. |
| docs/publisher-p0-media-consumption-history-fix.md | Evidências, contrato, integridade e relatório final. |

## Integridade e limites

Worktree: `C:\Users\Usuario\Documents\publisher-wt-integration`. Branch: `feature/publisher-p0-integration`. HEAD continua na base `29d72b1459b0dd110e0cb68919b87dd795f78b63`. Os commits de origem `f304dfa951b265e65fba458028a66176124d9d50` e `d4aa73611cb71f6c80d25ad4386edc74e218c6f0` permanecem nas branches/worktrees originais, limpas e nos mesmos HEADs; seu conteúdo integrado foi preservado com a correção descrita. Main permanece em `9f4671b28b6678da58a69bb25765314f6d6adadd`.

Nenhum arquivo unmerged ou marcador de conflito permanece. Nenhum dado operacional, OAuth, serviço Publisher operacional, ação remota Drive/TikTok, Analytics, commit, merge, push ou tag foi executado/alterado por esta tarefa. Os arquivos de QA ficam fora do índice. O relatório anterior de integração registra a falha pré-correção e foi preservado como evidência histórica.

## Relatório solicitado

```text
PUBLISHER_ONLY_SCOPE=true

INTEGRATION_BRANCH=feature/publisher-p0-integration
INTEGRATION_HEAD=29d72b1459b0dd110e0cb68919b87dd795f78b63
SOURCE_COMMITS_PRESERVED=true
ORIGINAL_WORKTREES_UNCHANGED=true

P0_ROOT_CAUSE_CONFIRMED=true
BYPASS_LOCATION=consumption_eligibility + cleanup_remote sem guard historico
SHARED_CONSUMPTION_GUARD_IMPLEMENTED=true

MEDIA_HISTORY_RECONCILIATION_REQUIRED_ENFORCED=true
FAKE_DRIVE_DELETE_CALLS_ON_BLOCKED_MEDIA=0
BLOCKED_MEDIA_MARKED_CONSUMED=false
HISTORICAL_EVIDENCE_PRESERVED=true

INDIVIDUAL_CONSUMPTION_PROTECTED=true
BULK_CONSUMPTION_PROTECTED=true
HUMAN_CONFIRMATION_DOES_NOT_BYPASS_GUARD=true
CONCURRENCY_PROTECTED=true
IDEMPOTENCY_PRESERVED=true
LEGITIMATE_CONSUMPTION_PRESERVED=true
PARTIAL_RESULTS_PRESERVED=true

P0_INTEGRATION_TEST_PASSED=true
AFFECTED_TARGETED_TESTS_PASSED=217
AFFECTED_TARGETED_TESTS_FAILED=0
AFFECTED_TARGETED_TESTS_ERRORS=0

FILES_CHANGED_THIS_TASK=8
STATIC_CHECKS_PASSED=true

OPERATIONAL_DB_WRITES=0
REAL_DRIVE_DELETIONS=0
TIKTOK_REMOTE_ACTIONS=0
ANALYTICS_CODE_CHANGED=false

COMMIT_CREATED=false
OPERATIONAL_BRANCH_UNCHANGED=true
MAIN_UNCHANGED=true

FULL_INTEGRATED_VALIDATION_PENDING=true
READY_FOR_SEPARATE_FULL_GATE=true
```
