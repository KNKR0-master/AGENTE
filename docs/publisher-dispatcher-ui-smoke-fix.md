# Publisher P0 - dispatcher corrigido e smoke integrado concluído

Solicitação de 2026-10-12. **TARGETED_VALIDATION_PASSED=true; READY_FOR_FINAL_FULL_GATE=true.**

O HTTP 404 de avaliação foi corrigido com uma alteração de produção de uma linha. Passaram cinco regressões do dispatcher, 23 regressões P0 e os quatro cenários de UI no Chrome Stable. Não houve commit ou ação operacional. A suíte completa e os 27 complementares não foram repetidos nesta tarefa; a validação final completa continua pendente.

## Contrato e correção

Em [dashboard_server.py](C:/Users/Usuario/Documents/publisher-wt-integration/dashboard_server.py:169), a expressão do dispatcher GET de lotes passou de `(preflight|review)` para `(preflight|review|consumption-evaluation)`. O encaminhamento continua sendo `PUBLISHER.get(self, query, path)`, com path separado e query via `parse_qs`, exatamente como o mecanismo existente.

| Método e rota | Auditoria |
|---|---|
| GET `/api/publisher/batches/{id}/consumption-evaluation` | Único encaminhamento ausente; corrigido. Implementação existente em PublisherAPI.get preservada. HTTP 200 JSON, payload compatível com o frontend e `remote_read_only=true`. |
| GET `/api/publisher/batches/{id}/preflight` | Já conectado; regressão HTTP aprovada. |
| GET `/api/publisher/batches/{id}/review` | Já conectado; regressão HTTP aprovada. |
| POST `/api/publisher/batches/{id}/register-published` | Já encaminhado por Handler.do_POST ao PublisherAPI.post; validado sem exclusão Fake. |
| POST `/api/publisher/batches/{id}/finalize-consumption` | Já encaminhado; confirmação destrutiva separada e validação de Origin preservadas e testadas. |
| POST `/api/publisher/cleanup` | Já encaminhado; smoke e regressões P0 confirmaram bloqueio histórico mesmo com confirmação humana. |

Nenhuma outra rota necessária foi encontrada desconectada. Não se duplicou a implementação de avaliação nem se modificaram regras de elegibilidade, guards, locks, checkpoints ou o JavaScript. O novo GET reutiliza o tratamento GET existente; identidades de conta, lote e mídia continuam verificadas pelo domínio. Os POSTs mantêm sua autorização explícita e a rejeição de Origin divergente. Nenhuma permissão de outro endpoint foi ampliada.

## Regressão pelo dispatcher real

Novo arquivo: [test_publisher_dispatcher_consumption.py](C:/Users/Usuario/Documents/publisher-wt-integration/tests/test_publisher_dispatcher_consumption.py).

Os testes compilam os métodos reais `Handler._api_get` e `Handler.do_POST` a partir do AST do arquivo de produção e os vinculam a um handler HTTP local. Isso executa os corpos efetivos dos métodos sem importar os globals operacionais do dashboard nem inicializar seu worker/banco. O PublisherAPI usa SQLite temporário e Fake Drive. O fallback para APIs fora de Publisher falha explicitamente se alcançado.

Cinco casos aprovados:

- Avaliação elegível por HTTP, repetida, com query corretamente encaminhada, batch/account/source/remote_file corretos, token de identidade e payload do frontend.
- Mídia com histórico remoto não reconciliado: HTTP 200 com item bloqueado por `MEDIA_HISTORY_RECONCILIATION_REQUIRED`; nenhum adapter criado, nenhuma exclusão ou estado CONSUMED.
- Lote misto: três elegíveis, um sem publicação registrada e um abandonado, mantendo IDs e bloqueios próprios de cada item.
- Preflight e review existentes permanecem acessíveis por HTTP.
- POSTs reais de registro/finalização: registro local não exclui; ausência de autorização separada retorna 400; Origin divergente retorna 400; autorização válida permite apenas consumo Fake.

Na avaliação, o estado lógico de todas as tabelas Publisher e do ledger de mutação é comparado antes/depois. As avaliações não modificam esses estados nem chamam exclusão no Fake Drive. A factory só é consultada para candidatos elegíveis.

## Correções do smoke

**Checkbox da Library:** confirmado em publisher.js que mídia reservada/com bloqueio histórico continua selecionável para inspeção; apenas mídia REMOTE_UNAVAILABLE é desabilitada nesse controle. A fixture agora exige checkbox habilitado e selecionado, aviso histórico visível, rejeição de criação de novo Draft, rejeição de reupload pelo backend, cleanup individual bloqueado e consumo em lote bloqueado mesmo com autorização humana. O reupload do sucessor já persistido retorna `DRAFT_ALREADY_PERSISTED`; as regressões P0 separadas também comprovam o bloqueio histórico de um sucessor reservado ainda não persistido. Não houve mudança da UI nem redução da proteção histórica.

**Drive Sync:** a fixture recebe configuração temporária completa: conta `fixture@example.invalid`, raiz `synthetic-root` e perfil/pasta `ceci`, correspondentes ao inventário sintético. Nenhuma credencial foi configurada e nenhuma chamada real ao Drive foi feita. As exceções anteriores `DRIVE_SYNC_SCOPE_INCOMPLETE` desapareceram sem supressão de logs ou retries.

O lote sintético já publicado foi colocado no Histórico de uma execução concluída. Na primeira execução, um clique tentou acessar o botão de revisão dentro de um detalhe ainda fechado. A navegação do fixture foi completada com clique no summary da execução; não se usou clique forçado nem alteração de produção. A primeira execução ficou em 3 aprovados/1 erro de locator, sem exceções de servidor; a reexecução completa aprovou os quatro cenários.

## Quatro cenários finais aprovados

| Cenário | Evidência validada |
|---|---|
| 1 - histórico remoto inseguro | Aviso visível, seleção sem conceder elegibilidade, revisão acessível pelo Histórico, avaliação sem 404 com item desabilitado e motivo histórico, individual/bulk/new Draft/reupload/cancelamento sem bypass. Histórico e MediaAsset preservados; zero exclusões Fake para a mídia bloqueada. |
| 2 - falha local | Dispensar confirmação não envia cancelamento. Aceitar envia uma única requisição e libera somente a reserva segura. Draft arquivado, snapshot e log preservados, mídia disponível para seleção. Zero exclusões Fake. |
| 3 - quatro publicados e um abandonado | Duas variantes isoladas: COMPLETE com quatro consumos Fake; PARTIAL com três consumos e uma falha Fake. Registro local de quatro publicações, avaliação reconhecida, abandonado fora dos elegíveis, autorização independente, alteração da seleção revoga a autorização, uma única finalização por variante, resultados por filename e ausência de falso sucesso global. Abandonado nunca consumido/excluído. |
| 4 - interface integrada | Queue, resolução com aviso de segurança, formulário de consumo único, ocultar/restaurar e navegação de lotes ocultos, duas requisições de apresentação esperadas, ausência de IDs duplicados e de erros JavaScript. Estados/histórico intactos. |

Chrome Stable real (`channel='chrome'`), perfis temporários, HTTP local em portas próprias e SQLite isolado. Nenhum teste utilizou a porta operacional 8765. Todos os efeitos de exclusão usaram Fake Drive.

## Execuções e integridade

| Execução final | Descobertos/aprovados | Falhas | Erros | Skips | Exceções inesperadas |
|---|---:|---:|---:|---:|---:|
| Dispatcher + regressões P0 | 28/28 | 0 | 0 | 0 | 0 |
| Smoke UI | 4/4 | 0 | 0 | 0 | 0 |

Os 28 direcionados são cinco novos testes e os 23 casos do módulo P0 existente; o smoke é contado separadamente. O direcionado terminou em 110,946 s e o smoke final em 35,177 s. Foram aceitas 49 e 91 conexões SQLite temporárias, respectivamente, sem bloqueios/violações de isolamento ou requisições externas do browser. A cobertura P0 inclui confirmação humana, consumo individual/bulk, preservação de evidência, revalidação sob lock, corrida de concorrência, checkpoints, idempotência e consumo parcial.

Python compile aprovou 46 arquivos; `node --check dashboard/publisher.js`, `git diff --check` e `git diff --cached --check` passaram. A comparação de AST prova que a única alteração de produção é um único literal de rota Publisher; todo o restante de dashboard_server.py, incluindo Analytics, é igual à base. Os 28 arquivos preparados anteriormente conservam seus SHA256. O relatório anterior permaneceu como evidência histórica.

Worktree exclusiva `C:/Users/Usuario/Documents/publisher-wt-integration`, branch `feature/publisher-p0-integration`, HEAD `29d72b1459b0dd110e0cb68919b87dd795f78b63`. Worktree operacional e ambas as fontes continuam com Git limpo, branches e HEADs anteriores. Main permanece em `9f4671b28b6678da58a69bb25765314f6d6adadd`.

Nenhum SQLite operacional foi aberto por testes. Não se leu o banco ativo para perseguir diferenças de hash nem se exigiu invariância byte-for-byte enquanto o serviço original o utiliza. A preservação operacional nesta tarefa baseia-se no isolamento das conexões/ações e no estado dos arquivos versionados. Registros reais e artifacts protegidos não foram consultados individualmente nem modificados por esta tarefa. Não se reiniciou o Publisher, executou OAuth, upload/Save Draft, migrations operacionais ou exclusão real.

Alterações versionáveis desta tarefa: uma linha em dashboard_server.py, o novo arquivo de regressão e este relatório. O runner/smoke corrigido e screenshots ficam na área QA ignorada. A suíte completa e os 27 complementares aguardam a tarefa separada de gate final; não se declara pronta a integração para commit ou implantação com base apenas neste direcionado.

## Relatório solicitado

```text
PUBLISHER_ONLY_SCOPE=true
INTEGRATION_BRANCH=feature/publisher-p0-integration
INTEGRATION_HEAD=29d72b1459b0dd110e0cb68919b87dd795f78b63
DISPATCHER_ROOT_CAUSE_CONFIRMED=true
CONSUMPTION_EVALUATION_ROUTE_FIXED=true
ADDITIONAL_MISSING_ROUTES_FOUND=0
ROUTES_FIXED=GET /api/publisher/batches/{id}/consumption-evaluation
REAL_DISPATCHER_TEST_PASSED=true
HTTP_404_RESOLVED=true
EVALUATION_READ_ONLY_ENFORCED=true
CHECKBOX_HARNESS_EXPECTATION_CORRECTED=true
HISTORICAL_MEDIA_BLOCK_STILL_ENFORCED=true
DRIVE_SYNC_FIXTURE_CORRECTED=true
UNEXPECTED_FIXTURE_ERRORS=0
UI_SMOKE_DISCOVERED=4
UI_SMOKE_PASSED=4
UI_SMOKE_FAILED=0
UI_SMOKE_ERRORS=0
HISTORICAL_P0_REGRESSION_PASSED=true
BLOCKED_FAKE_DRIVE_DELETE_CALLS=0
BLOCKED_MEDIA_MARKED_CONSUMED=false
AFFECTED_TARGETED_TESTS_PASSED=28
AFFECTED_TARGETED_TESTS_FAILED=0
AFFECTED_TARGETED_TESTS_ERRORS=0
STATIC_CHECKS_PASSED=true
ORIGINAL_WORKTREE_UNCHANGED=true
SOURCE_WORKTREES_UNCHANGED=true
OPERATIONAL_DB_WRITES=0
REAL_DRIVE_DELETIONS=0
TIKTOK_REMOTE_ACTIONS=0
ANALYTICS_CODE_CHANGED=false
COMMIT_CREATED=false
MAIN_UNCHANGED=true
TARGETED_VALIDATION_PASSED=true
READY_FOR_FINAL_FULL_GATE=true
FULL_PUBLISHER_SUITE_EXECUTED_THIS_TASK=false
COMPLEMENTARY_27_EXECUTED_THIS_TASK=false
```

Evidência: [direcionados](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/dispatcher-ui-fix/gate-targeted-result.json), [smoke final](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/dispatcher-ui-fix/gate-UI-final-result.json), [auditoria de integridade](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/dispatcher-ui-fix/integrity.json), [fixture corrigido](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/dispatcher-ui-fix/ui_smoke.py), [captura de resultados parciais](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/dispatcher-ui-fix/test_03_four_publications_one_abandoned_separate_delete_authorization_and_partial_results.png).

Trabalho encerrado após o relatório, sem commit, merge, push ou tag.
