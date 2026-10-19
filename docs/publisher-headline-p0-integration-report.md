# Publisher — integração da headline com o hardening P0

Integração preparada sem commit ou deploy. Branch `feature/publisher-p0-integration`; HEAD permanece `aa1ff7010fe55b3e363ad5f11d5e127ac5a3061a`. Origem: `bdf0e57e61cd874cbd3c3f3f1a20dac24a9a11da`.

## Preflight e conflitos

Destino e worktree da headline estavam limpos. O commit de origem existia e não estava incorporado, inclusive por equivalência de patch. Nenhuma operação Git estava incompleta. Hashes dos 274 arquivos iniciais da integração e dos arquivos não ignorados das quatro worktrees protegidas foram registrados antes da aplicação.

Foi executado `git cherry-pick --no-commit`. Os dois conflitos ocorreram exclusivamente nos imports de `queue_policy.py` e `service.py`: foram mantidos `datetime`, `media_history_blocker`, `assert_media_history_allowed` e `MediaHistoryBlocked`, acrescentando `json` e `queue_preparation_diagnostic`. Não houve escolha integral de ours/theirs, alteração de regra de domínio ou correção adicional de produção. Zero arquivos unmerged e zero conflitos semânticos restantes.

## Integridade dos contratos

Onze arquivos staged correspondem exatamente aos blobs da headline. Seis arquivos combinam os dois desenvolvimentos: UI, posting review, queue policy, service e os dois testes compartilhados. A comparação AST confirmou que os guards P0 de retomada pré-upload, autorização de upload, cancelamento e capacidades permanecem intactos. Os métodos de teste P0 preexistentes nos arquivos compartilhados foram preservados por comparação AST.

Nove módulos P0 permanecem byte a byte iguais ao HEAD inicial: worker, lifecycle, cleanup_authorization, persistence_identity, batch_consumption, media_history, manual_resolution, reconciliation_evidence e http. A integração não altera a autoridade de confiança ou o protocolo de recibos.

Renderer, configuração, ferramentas de mídia, validação temporal e preparação correspondem ao commit aprovado da headline. Mantidos: libx264 medium/CRF 18, yuv420p, até quatro threads efetivas de vídeo, timeout próprio de encode com máximo de 600 segundos e demais limites preservados. NVENC não foi habilitado. Não houve nova otimização ou benchmark.

As regressões confirmaram que threads efetivas participam da identidade do renderer; cache incompatível exige novo artifact sem sobrescrever o anterior; timeout isolado não invalida cache válido; snapshot e original sintético permanecem preservados. CFR/VFR, contagem de frames, timestamps, áudio, dimensões e duração mantêm a validação integral. Headline habilitada não utiliza original como fallback silencioso.

## Validação direcionada

- 310 testes distintos da seleção afetada: 310 aprovados em 974,143 segundos, sem falhas, erros ou skips.
- Seis testes cruzados novos: seis aprovados em 28,844 segundos na rodada final.
- Total da validação final: 316 aprovados, zero falhas, erros ou skips.
- Chrome Stable isolado: diagnóstico da headline na Queue, confirmação de cancelamento e consumo parcial de lote aprovados.
- 483 conexões SQLite aceitas nas duas rodadas finais, somente em memória ou QA temporário; zero bloqueios, violações de isolamento, erros inesperados de fixture e requisições de navegador fora de loopback. Porta operacional 8765 proibida.
- Compile de 33 arquivos Python, três `node --check`, `git diff --check` e `git diff --cached --check` aprovados.

O cenário sintético equivalente à falha do Draft #50 habilitou headline, provocou timeout real de encode, restaurou o Draft a DRAFT, pausou a Execution e confirmou o diagnóstico da tentativa anterior sem reescrever o erro próprio do Draft. A retomada segura produziu e validou derivado com o prazo próprio, snapshot intacto, persistência UNCONFIRMED e nenhum upload.

Os testes cruzados também confirmaram: headline pronta/cache não contornam histórico ou identidade; cancelamento local elegível não concede consumo; observação de Save sem prova mantém UNCONFIRMED; recibo Fake vincula o hash do artifact realmente derivado e permanece restrito ao SQLite da fixture; mesmo após promoção confiável, cleanup exige consentimento independente.

A primeira execução dos seis testes novos teve três aprovados, uma falha e dois erros por pressupostos incorretos das novas fixtures. Corrigidos somente os cenários: cancelamento após pausa usa eligible_only e mantém a asserção de bloqueio do cancelamento geral; observação de Save recebe estado/checkpoint sintético de transferência; prova Fake recebe checkpoints sintéticos de transferência/Save e checklist antes da promoção real do domínio. Nenhum teste preexistente ou asserção de segurança foi enfraquecido; PERSISTED não foi semeado. A rodada inicial foi preservada em gate-X-result.json, e a rodada corrigida em gate-X2-result.json.

## Limites e preservação

A suíte Publisher completa e os 27 complementares não foram executados nesta tarefa; ficam para o gate final separado. Os números acima descrevem somente a seleção direcionada e os seis cruzados, sem contar novamente a primeira rodada das novas fixtures.

Nenhum banco, registro, reserva, snapshot ou artifact operacional foi acessado ou modificado pelos testes. Draft #50, Execution #15, MediaAsset #855, Batch #12, Drafts #44/#49/#51/#52 e MediaAssets #924/#929/#926 foram preservados pelo isolamento, sem consultas ao banco operacional. Não houve upload, Save Draft, OAuth, cleanup, exclusão Drive real ou restart. As operações simuladas de Studio/Drive ocorreram exclusivamente em fixtures.

Worktrees operacional, headline, media-safety e batch-consumption mantêm HEAD, branch, status, índice e hashes dos arquivos não ignorados. Todas as referências Git, inclusive main e a branch operacional original, permanecem iguais ao preflight. Nenhum commit, merge na operacional/main, push ou tag foi realizado.

O Studio real continua sem emissor confiável implementado. A prova Fake demonstra o contrato em ambiente isolado; registros reais sem prova inequívoca permanecem UNCONFIRMED. Pronto para o gate completo final separado; deploy operacional não aprovado.

## Relatório

```ini
PUBLISHER_ONLY_SCOPE=true
INTEGRATION_BRANCH=feature/publisher-p0-integration
INITIAL_HEAD=aa1ff7010fe55b3e363ad5f11d5e127ac5a3061a
HEADLINE_SOURCE_COMMIT=bdf0e57e61cd874cbd3c3f3f1a20dac24a9a11da
P0_HARDENING_PRESENT=true
HEADLINE_CHERRY_PICK_PREPARED=true
UNMERGED_FILES_REMAINING=0
CONFLICTS_FOUND=2
CONFLICTS_RESOLVED=2
SEMANTIC_CONFLICTS_REMAINING=0
HEADLINE_TIMEOUT_PRESERVED=true
FOUR_THREAD_OPTIMIZATION_PRESERVED=true
RENDER_CACHE_IDENTITY_VALIDATED=true
CFR_VFR_VALIDATION_PRESERVED=true
QUEUE_ERROR_DIAGNOSTICS_VALIDATED=true
HISTORICAL_MEDIA_GUARD_PRESERVED=true
TRUSTED_PERSISTENCE_PROOF_PRESERVED=true
NO_BLIND_REUPLOAD=true
AUTO_CLEANUP_GUARD_PRESERVED=true
BATCH_CONSUMPTION_PRESERVED=true
CROSS_FEATURE_TESTS_PASSED=6
TARGETED_TESTS_DISCOVERED=316
TARGETED_TESTS_PASSED=316
TARGETED_TESTS_FAILED=0
TARGETED_TESTS_ERRORS=0
STATIC_CHECKS_PASSED=true
INTEGRATED_DIFF_AUDITED=true
ORIGINAL_WORKTREE_UNCHANGED=true
SOURCE_WORKTREES_UNCHANGED=true
OPERATIONAL_DB_WRITES=0
TIKTOK_REMOTE_ACTIONS=0
DRIVE_REMOTE_WRITES=0
ANALYTICS_CODE_CHANGED=false
COMMIT_CREATED=false
MAIN_UNCHANGED=true
REMOTE_PUSH_PERFORMED=false
READY_FOR_FINAL_FULL_GATE=true
READY_FOR_OPERATIONAL_DEPLOYMENT=false
```

Evidências ignoradas: `data/publisher/qa/headline-p0-integration/` — preflight, hashes, patches, resolução de conflitos, seleção, logs/JSONs das rodadas, checks estáticos, auditoria integrada e postflight. Somente implementação, testes e documentação entram no diff revisável.
