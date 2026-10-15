# Publisher P0 — persistência remota e recuperação operacional

Worktree exclusiva: `C:/Users/Usuario/Documents/publisher-wt-integration`.
Branch: `feature/publisher-p0-integration`; HEAD: `ff539f330c4121f7f91a196867c3f04607cac027`.
Implementação consolidada, sem commit, deploy ou ação remota real. O relatório anterior e os tracebacks das 58 ocorrências e dos dois smokes foram examinados; resultados brutos anteriores permanecem intactos.

## Diagnóstico consolidado

A classificação foi registrada antes da edição em `data/publisher/qa/persistence-consolidation/root-causes-before-edit.json`. As 58 ocorrências correspondem a 51 casos distintos e sete repetições de módulo na descoberta CLI. Não são 58 defeitos independentes.

| Grupo | Ocorrências / casos distintos | Contrato anterior alterado | Natureza e correção compartilhada | Representante |
|---|---:|---|---|---|
| G1 — identidade de persistência nas fixtures | 31 / 28 | Flags, legenda, URL e screenshot antes bastavam para semear sucesso; confirmação sem prova agora conserva UNCONFIRMED | Fixtures antigas, mais uma lacuna real: o verificador recusava inclusive prova independente. Protocolo de recibo verificável e promoção atômica; helper positivo central usa esse caminho. Fixtures históricas respeitam uma reserva ativa por vez; fontes derivadas preservam o registro imutável real do worker. Sem prova, os negativos continuam bloqueados. | `PersistenceTests.test_transition_and_idempotence`; `TrustedPersistenceTests.test_fresh_receipt_promotes_atomically_and_separate_consent_is_still_required` |
| G2 — limpeza automática | 8 / 5 | `auto_cleanup=true` e retomada automática de reconciliação/cleanup foram retirados | Expectativas antigas incompatíveis com P0. Manter rejeição e verificar ausência de comandos, transferências, exclusões e alteração de Drafts. Testes de pausa/ordenação que não requerem destruição usam false. Nenhum retorno da funcionalidade automática. | `LocalPreparationResumeTests.test_local_failure_can_resume_with_cleanup`; negativos P0 de execuções legadas |
| G3 — resultado após Save observado | 8 / 7 | Save + legenda visível não comprovam mídia persistida | Expectativa antiga de sucesso automático. Tratar MANUAL_ACTION_REQUIRED/UNCONFIRMED conservadoramente, mantendo asserções de upload, caption, Save, ausência de Publish, fonte original/derivada e snapshot. Quando o teste exige consumo positivo, aplicar prova independente depois da revisão e consentimento separado. | `PersistenceTests.test_normal_adapter_upload_caption_save_without_publish`; `HeadlinePreparationTests.test_worker_derived_e2e_persistence_and_cleanup` |
| G4 — retomada apenas local | 5 / 5 | Bloqueador passou de AUTO_CLEANUP_REQUIRED para PREUPLOAD_RESUME_NOT_PROVEN_SAFE | Asserções antigas do código de erro; guard já correto. Atualizar o contrato e manter negativos de efeito remoto, ledger e falha obsoleta. A fixture de ordenação registra o código HEADLINE auditável exigido. | `LocalPreparationResumeTests.test_prior_remote_checkpoint_blocks_local_resume` |
| G5 — consentimento destrutivo | 5 / 5 | Cleanup agora exige autorização específica auditada transportada pelo chamador | Fixtures antigas não forneciam handle e não chegavam aos testes de corrida/lock. Emitir consentimento explícito antes de injetar a corrida, transportar o handle e conservar zero lookups/deletes indevidos. Mudança de identidade é rejeitada pelo consentimento vinculado, antes do guard seguinte. | `PublisherP0ConsumptionIntegrationTests.test_identity_change_between_preflight_and_claim_fails_closed` |
| G6 — correspondência composta | 1 / 1 | Caption + duração + horário não comprovam bytes remotos | Expectativa antiga de REMOTE_EXISTS_CONFIRMED. O coletor real implementado conserva REMOTE_STATE_UNKNOWN; duplicidade continua ambígua. Observação não muda Draft, mídia nem permissões. | `ReadOnlyVerificationTests.test_strong_and_ambiguous_matches_record_evidence_only` |

Nenhuma das 58 ocorrências foi atribuída a infraestrutura original. Problemas introduzidos durante esta implementação foram registrados nos resultados intermediários: conexão de reinicialização não fechada no teste novo, fixture histórica violando unicidade da reserva, marcador local sem o prefixo exigido e expectativa do bloqueador anterior. As correções mantiveram os guards. Os dois antigos fluxos de retomada com limpeza automática não foram restabelecidos: seus testes agora comprovam a rejeição segura, e os fluxos positivos de resolução e consumo são exercitados separadamente.

## Contrato de prova independente

`persistence_identity.py` separa recibo do emissor, evidência registrada e confirmação humana. O recibo versão 1 contém autoridade, operação SAVE_DRAFT, identidade remota específica, SHA-256 dos bytes remotos, unicidade, desafio/nonce, emissão, expiração de até cinco minutos e assinatura HMAC-SHA256 sobre JSON canônico. A assinatura não substitui a confiança no emissor: a chave precisa pertencer a uma autoridade previamente confiável.

O vínculo inclui conta e handle, Draft, MediaAsset, identidade canônica Drive, checksum/tamanho, fonte imutável e seu SHA/artifact SHA, caption/snapshot, execução, checkpoint remoto mais recente e digest do ledger. O verificador compara integralmente o vínculo, exige identidade de conta verificada, fonte da execução atual, evidência recente COMPLETE com candidato único e fingerprint do recibo. Rejeita outra conta/Draft/mídia/operação, identidade genérica, ambiguidades, adulteração, provas expiradas/futuras e reaproveitamento de identidade remota ou desafio em outro Draft ou em operação posterior do mesmo Draft.

A autoridade Fake existe somente em `tests/publisher_security_fixture.py`: chave aleatória por fixture, injetada pelo código de teste para o arquivo SQLite temporário exato. A produção não importa o Fake, não tem chave confiável configurada e não oferece rota, flag de ambiente ou checkbox para criar essa confiança. O leitor Fake compara bytes independentes, emite identidade sintética específica e assina o recibo; `record_trusted_persistence_evidence` registra a evidência append-only existente. Nenhum identificador TikTok real foi inventado.

A promoção toma BEGIN IMMEDIATE, verifica novamente a prova sob lock e grava estado PERSISTED, identidade remota, timestamps/checklist, mídia RESERVED e auditoria em uma transação. A resolução supervisionada termina o item local e, quando todos os itens estão encerrados, a execução. Checkpoints e ledger anteriores são preservados e vinculados à prova; não são fabricadas conclusões históricas de operações. Falha de auditoria reverte promoção, mídia e execução. Concorrência gera uma confirmação auditada; replay da mesma confirmação é idempotente. Alterar posteriormente fonte, snapshot, caption, checkpoint ou ledger invalida a identidade para consumo.

Legenda, URL genérica, screenshot, confirmação de transferência e observação ambígua permanecem insuficientes. Uma declaração humana é auditada em MANUAL_RESOLUTION, sem se converter em prova técnica ou consentimento de exclusão. CONFIRM_EXISTS só fica disponível quando o mesmo verificador do domínio aceita a evidência corrente; a rota revalida sob lock.

## Recuperação operacional e UI

A UI distingue transferência confirmada, persistência não confirmada e revisão necessária. O formulário usa “Confirmar persistência com prova independente verificada” e “Encerrar a tarefa local e arquivar este rascunho, preservando o histórico”. O ajuste de rádio é restrito ao diálogo de resolução.

UNKNOWN registra a decisão e mantém a incerteza/reserva. ABANDON exige confirmação, justificativa, checkpoint/identidades atuais e reconhecimento de possível existência remota. Arquiva o Draft, encerra a reserva local existente e registra o item como FAILED com ADMINISTRATIVELY_CLOSED_REMOTE_UNCONFIRMED. A execução só vai para COMPLETED se nenhum item ficar aberto. PERSISTED não é atribuído; os checkpoints e a proteção histórica continuam bloqueando nova reserva/upload/cleanup. Fechar uma tarefa não retoma outras tarefas automaticamente e não autoriza consumo. A retomada automática continua restrita à falha local comprovada anterior ao upload.

O Studio real não foi acessado. Seu coletor implementado continua sem prova suficiente e sem autoridade de recibos; novos resultados reais não verificados continuam UNCONFIRMED. O operador pode revisar, registrar UNKNOWN ou encerrar administrativamente quando autorizado pelo domínio, mas ainda não há comprovação técnica real que habilite persistência/consumo. Essa limitação permanece como bloqueador de deploy operacional.

## Validação e evidências

Resultados consolidados: **70 P0 aprovados, 166 regressões direcionadas aprovadas e cinco smokes Chrome aprovados; zero falhas/erros pendentes**. As 166 regressões compreendem 58 ocorrências originais (51 casos distintos + sete repetições CLI), 98 casos vizinhos dos contratos afetados e 10 casos novos de protocolo. São 229 casos unitários distintos por identidade lógica, mais sete repetições CLI e cinco cenários browser.

| Seleção final | Evidência e resultado |
|---|---|
| Protocolo + P0 | `gate-protocol-final-result.json`: 80/80; os 70 P0 e os 10 novos casos, incluindo replay posterior no mesmo Draft |
| Casos originais | `gate-affected-final-result.json`: 51/51 |
| Repetições CLI + Chrome | `gate-finish-smokes-result.json`: 12/12, sendo sete repetições CLI e cinco smokes |
| Vizinhos dos contratos alterados | `gate-nearby-result.json`: bruto 97/98 + uma falha de expectativa da fixture com dois itens; reexecução reparada aprovada em `gate-final-fixtures-result.json` (7/7) |
| Histórico positivo sem flags artificiais | `gate-historical-final-result.json`: 1/1; a promoção positiva histórica também usa exclusivamente o protocolo |
| Integridade/estática | `static-integrity.json`: compile de 23 Python, três JavaScript, diff/cached checks, staging vazio e worktrees/HEAD/main preservados |

A falha adjacente foi introduzida ao interpretar uma fixture de dois itens como tarefa única: esperar COMPLETED depois de fechar apenas um deles estava errado. O teste conserva PAUSED para a execução, exige que o item selecionado esteja encerrado com auditoria, e que o outro permaneça WAITING_MANUAL. O caso novo de tarefa única comprova COMPLETED. O resultado bruto 97/98 permanece intacto; `final-results.json` aponta a evidência da reexecução para o caso reparado, sem reescrever resultados antigos. Os resultados intermediários de desenvolvimento não constituem o gate final. Não houve descoberta integral de 829 testes. As listas de seleção ficam em `affected-test-names.json`, `cli-duplicate-test-names.json` e `nearby-test-names.json`; logs/resultados intermediários são preservados.

Os cinco smokes usam Chrome Stable, perfil temporário, porta própria, API/dispatcher reais locais, SQLite temporário e Fake Studio/Drive: (1) Save sem prova mantém incerteza e explica transferência; (2) cancelamento seguro após falha local; (3) promoção de Draft novo pelo formulário, com prova Fake e sem semear PERSISTED; (4) UNKNOWN e encerramento administrativo com guard histórico; (5) registro humano e consumo parcial com item incerto bloqueado e consentimento destrutivo separado. A mídia positiva do cenário 5 também chega a PERSISTED pelo protocolo, sem SQL de semeadura.

O runner intercepta SQLite e rede, permite somente bases de fixture/temporárias e loopback, proíbe a porta operacional 8765 e bloqueia requisições de browser externas. Os trabalhos anteriores foram copiados para `baseline/`; o diff incremental fica em `incremental-diff/`. As proteções de auto cleanup, execuções legadas, consentimento, histórico sob lock, idempotência e concorrência permanecem. `worker.py`, `http.py`, `batch_consumption.py`, `media_history.py`, `cleanup_authorization.py`, os 45 testes de hardening, os 25 de consumo e os dois relatórios anteriores permanecem byte a byte iguais ao preflight.

## Relatório final

```text
PUBLISHER_ONLY_SCOPE=true
CURRENT_BRANCH=feature/publisher-p0-integration
CURRENT_HEAD=ff539f330c4121f7f91a196867c3f04607cac027
PREEXISTING_WORK_PRESERVED=true
CORE_FAILURES_ORIGINAL=24
CORE_ERRORS_ORIGINAL=34
ROOT_CAUSE_GROUPS_FOUND=6
TESTS_AFFECTED_PER_GROUP=G1:31;G2:8;G3:8;G4:5;G5:5;G6:1
TRUSTED_EVIDENCE_CONTRACT_DEFINED=true
FAKE_TRUSTED_PROOF_ACCEPTED=true
INVALID_PROOF_REJECTED=true
ATOMIC_PERSISTENCE_PROMOTION_VALIDATED=true
REAL_STUDIO_UNIQUE_PROOF_AVAILABLE=false
UNVERIFIED_REAL_DRAFT_REMAINS_UNCONFIRMED=true
TRANSFER_VS_PERSISTENCE_UI_CLARIFIED=true
MANUAL_RESOLUTION_FLOW_VALIDATED=true
ADMINISTRATIVE_CLOSURE_DOES_NOT_AUTHORIZE_REUPLOAD=true
HISTORICAL_MEDIA_GUARD_PRESERVED=true
AUTO_CLEANUP_BYPASS_CLOSED=true
UNAUTHORIZED_DRIVE_DELETIONS=0
BLIND_REUPLOADS=0
P0_TARGETED_TESTS_PASSED=70
AFFECTED_REGRESSIONS_PASSED=166
AFFECTED_REGRESSIONS_FAILED=0
AFFECTED_REGRESSIONS_ERRORS=0
CHROME_SMOKES_PASSED=5
CHROME_SMOKES_FAILED=0
CHROME_SMOKES_ERRORS=0
REMAINING_FUNCTIONAL_BLOCKERS=REAL_STUDIO_TRUSTED_PROOF_ISSUER_UNAVAILABLE
REMAINING_HARNESS_BLOCKERS=NONE
STATIC_CHECKS_PASSED=true
OPERATIONAL_DB_WRITES=0
TIKTOK_REMOTE_ACTIONS=0
DRIVE_REMOTE_WRITES=0
ANALYTICS_CODE_CHANGED=false
COMMIT_CREATED=false
MAIN_UNCHANGED=true
MERGE_PERFORMED=false
READY_FOR_SEPARATE_FULL_GATE=true
READY_FOR_OPERATIONAL_DEPLOYMENT=false
```
