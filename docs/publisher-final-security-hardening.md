# Publisher P0 — final security hardening

Implementação isolada em `publisher-wt-integration`, branch `feature/publisher-p0-integration`, base `ff539f330c4121f7f91a196867c3f04607cac027`. Sem commit, deploy ou alteração operacional.

## Decisão e limitações

P0-A fechado no domínio: preparação rejeita `auto_cleanup=true`; execuções antigas com flag diferente de zero não podem executar nem retomar esse modo. Comandos antigos de reconciliação/cleanup automático são bloqueados antes de abrir adapters. O consumo explícito exige confirmação destrutiva própria, token da identidade apresentada e uma autorização registrada no ledger. A autorização vincula operação, Draft, Batch, conta, MediaAsset e identidade do arquivo Drive; o worker transporta seu identificador. Histórico, identidade e autorização são revalidados sob `BEGIN IMMEDIATE`, inclusive depois da consulta de metadata e imediatamente antes da exclusão. Publicação humana, cancelamento e ocultação não emitem consentimento.

P0-B fechado pelo fallback conservador autorizado: a superfície implementada em `draft_listing.py` observa legenda, duração e horário de atualização, mas não fornece identidade individual verificável do Draft nem dos bytes remotos. Mesmo uma correspondência única desses atributos pode ser outra mídia ou um Draft anterior atualizado. Não é tecnicamente suficiente para confirmar a mídia pretendida. Nenhum identificador TikTok foi inventado e nenhum sucesso remoto foi presumido.

`StudioPage.confirm_durable` registra observação não confirmada e interrompe após Save Draft. O domínio `confirm_received` não promove persistência. A verificação supervisionada conserva candidatos como observações; uma correspondência composta única passa a `REMOTE_STATE_UNKNOWN / MATCH_EVIDENCE_INSUFFICIENT`. `reconcile_received` também recusa a promoção por essa superfície, mesmo com confirmação humana e evidência antiga classificada como existência confirmada. A UI desabilita essa confirmação e explica que a origem foi preservada. Revisão, inspeção sem escrita remota, decisão UNKNOWN e abandono supervisionado continuam disponíveis; abandono mantém o bloqueio histórico para novo uso.

Limitação operacional explícita: a superfície atual não consegue qualificar novos Drafts nem registros legados apoiados somente em flags/legenda/URL para consumo. Esses registros continuam bloqueados. Observar transferência ou Save Draft não significa falha do upload e não autoriza retry. O snapshot congelado, fontes de upload, checkpoints e ledgers são preservados; não houve migração de dados operacionais.

O guard histórico exige prova independente vinculada ao snapshot, à conta, à fonte imutável, ao checkpoint remoto, à execução e ao ledger de mutações. Alterações posteriores invalidam uma prova anterior. Não existe emissor dessa prova na superfície atual do Studio. Os testes positivos de consumo semeiam **somente fixtures previamente verificadas por um leitor Fake de bytes**, identificadas como `FAKE_REMOTE_MEDIA_READER`; eles validam autorização, lock, idempotência, concorrência e falha parcial do pipeline de consumo. Não representam confirmação real do Studio ou implementação de identidade inequívoca nessa interface.

## Auditoria dos bypasses no commit-base

- `http.py` repassava a opção de execução `auto_cleanup` ao enqueue; `worker.py` usava a flag persistida após `prepare` e nos caminhos antigos `reconcile`/`cleanup-reconciled`, chegando a `cleanup_remote` sem consentimento de exclusão. O domínio de cleanup validava persistência/histórico, mas não exigia uma autorização destrutiva própria. Todos esses caminhos foram auditados; só restam chamadas explícitas do worker e do consumo em lote, ambas com autorização. A exclusão independente da Biblioteca mantém seu contrato anterior de confirmação e seus guards.
- `StudioPage.confirm_durable` promovia por texto da lista, URL `/tiktokstudio/content` e `caption.count() >= 1`; `confirm_received` aceitava checklist/URL e um log com screenshot. `reconcile_received` também tinha fallback para log/screenshot. `media_history.remote_history` tratava flags locais como persistência suficiente. Agora nenhuma dessas combinações autoriza promoção ou consumo.

## Regressões e isolamento

`tests/test_publisher_security_hardening.py` cobre flags novas/antigas, rota direta, retomada, comandos antigos, cleanup sem confirmação/token/handle, autorização vinculada e corrompida, revalidação sob lock, histórico posterior/arquivado, URL genérica, legenda repetida, conta incorreta, candidato antigo, transferência incerta, timeout/falha após Save Draft, reinício, ausência de retry e reconciliação supervisionada conservadora.

`tests/test_publisher_batch_consumption.py` conserva os testes de consumo autorizado, autorização independente, consumo parcial, cancelamento local, ocultação, repetição, concorrência, timeout/falha, estado pendente, 404, receipts e recuperação local. Suas fixtures positivas agora representam identidade independentemente verificada por Fake; a finalização individual recebe consentimento explícito.

Runner isolado: `data/publisher/qa/security-hardening/run_gate.py`, copiado do runner do gate anterior. SQLite restrito a diretórios temporários em QA; auditoria de rede bloqueia destinos não locais e porta operacional 8765. Nenhum navegador real ou conexão remota foi usado nesta rodada. A suíte completa Publisher, os 27 complementares e smokes integrados permanecem pendentes. Os números do gate anterior não foram reaproveitados como validação deste diff. Fixtures antigas que esperam promoção automática ou cleanup sem consentimento precisarão seguir o novo contrato no gate completo; essa compatibilidade global ainda não foi avaliada.

Comando direcionado final:

```powershell
python data/publisher/qa/security-hardening/run_gate.py security-final tests.test_publisher_security_hardening tests.test_publisher_batch_consumption
```

As evidências locais ficam em `data/publisher/qa/security-hardening`: preflight, resultado final JSON/log, checks estáticos e hashes do código validado. O erro inicial de fixture (target `draft` em vez de `DRAFT`) foi corrigido; as execuções iniciais não substituem o resultado final.

## Relatório solicitado

Resultado final: **70 testes direcionados distintos, 70 aprovados, zero falhas, erros ou skips**. 100 conexões SQLite aceitas exclusivamente nas fixtures; zero conexões bloqueadas, violações de isolamento, falhas inesperadas de fixture ou requisições remotas. Checks estáticos e comparação dos hashes do código aprovado concluídos.

`MANUAL_RECONCILIATION_AVAILABLE=true` refere-se à revisão/inspeção e às decisões UNKNOWN/ABANDON. A promoção CONFIRM_EXISTS permanece bloqueada pela ausência de prova remota suficiente. `LEGITIMATE_CLEANUP_PRESERVED=true` e `BATCH_CONSUMPTION_PRESERVED=true` referem-se ao pipeline de identidades independentemente comprovadas; não qualificam os Drafts reais/legados do Studio para exclusão.

```text
PUBLISHER_ONLY_SCOPE=true
CURRENT_BRANCH=feature/publisher-p0-integration
CURRENT_HEAD=ff539f330c4121f7f91a196867c3f04607cac027
BASE_COMMIT=ff539f330c4121f7f91a196867c3f04607cac027
AUTO_CLEANUP_ROOT_CAUSE=EXECUTION_FLAG_REACHED_REMOTE_DELETE_WITHOUT_SEPARATE_DESTRUCTIVE_CONSENT
AUTO_CLEANUP_BYPASS_CLOSED=true
AUTO_CLEANUP_TRUE_BEHAVIOR=REJECTED_IN_PREPARE_ENQUEUE_LEGACY_DISPATCH_AND_RESUME
DESTRUCTIVE_AUTHORIZATION_REQUIRED=true
WORKER_CLEANUP_GUARD_ENFORCED=true
LEGACY_AUTO_CLEANUP_STATE_PROTECTED=true
PERSISTENCE_IDENTITY_ROOT_CAUSE=CAPTION_COUNT_GENERIC_URL_AND_LOG_SCREENSHOT_ACCEPTED_AS_PERSISTENCE
UNIQUE_REMOTE_IDENTITY_PROOF_IMPLEMENTED=false
CONSERVATIVE_PERSISTENCE_BLOCK_IMPLEMENTED=true
GENERIC_URL_REJECTED_AS_IDENTITY_PROOF=true
CAPTION_ONLY_PROOF_REJECTED=true
AMBIGUOUS_PERSISTENCE_REMAINS_UNCONFIRMED=true
NO_BLIND_REUPLOAD=true
MANUAL_RECONCILIATION_AVAILABLE=true
HISTORICAL_MEDIA_GUARD_PRESERVED=true
BATCH_CONSUMPTION_PRESERVED=true
LEGITIMATE_CLEANUP_PRESERVED=true
FILES_CHANGED=13
TESTS_PREPARED=70
TARGETED_TESTS_PASSED=70
TARGETED_TESTS_FAILED=0
TARGETED_TESTS_ERRORS=0
STATIC_CHECKS_PASSED=true
ORIGINAL_WORKTREE_UNCHANGED=true
SOURCE_WORKTREES_UNCHANGED=true
OPERATIONAL_DB_WRITES=0
TIKTOK_REMOTE_ACTIONS=0
DRIVE_REMOTE_WRITES=0
REAL_DRIVE_DELETIONS=0
ANALYTICS_CODE_CHANGED=false
COMMIT_CREATED=false
MAIN_UNCHANGED=true
MERGE_PERFORMED=false
FULL_VALIDATION_PENDING=true
READY_FOR_SEPARATE_FULL_SECURITY_GATE=true
READY_FOR_OPERATIONAL_DEPLOYMENT=false
```

Arquivos alterados:

- `dashboard/publisher.js`
- `docs/publisher-final-security-hardening.md`
- `src/publisher/batch_consumption.py`
- `src/publisher/cleanup_authorization.py`
- `src/publisher/http.py`
- `src/publisher/lifecycle.py`
- `src/publisher/media_history.py`
- `src/publisher/persistence_identity.py`
- `src/publisher/service.py`
- `src/publisher/worker.py`
- `tests/publisher_security_fixture.py`
- `tests/test_publisher_batch_consumption.py`
- `tests/test_publisher_security_hardening.py`

