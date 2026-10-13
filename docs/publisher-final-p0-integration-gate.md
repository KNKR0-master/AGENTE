# Publisher - gate final de integração P0

Data da solicitação: 2026-10-12. Decisão: **BLOCKED_BY_PRODUCT_DEFECT**.

A proteção P0, a suíte Publisher completa e os 27 complementares passaram. O smoke identificou um defeito funcional de roteamento: o botão de avaliação do consumo recebe HTTP 404 no dispatcher real. Não houve correção de produção, commit ou ação operacional nesta tarefa. A integração não está pronta para commit separado.

## Defeito funcional comprovado

Em [dashboard_server.py](C:/Users/Usuario/Documents/publisher-wt-integration/dashboard_server.py:169), `Handler._api_get` encaminha os endpoints de lote `preflight` e `review`, mas não `consumption-evaluation`. A rota é implementada em [http.py](C:/Users/Usuario/Documents/publisher-wt-integration/src/publisher/http.py:335) e chamada pelo botão em [publisher.js](C:/Users/Usuario/Documents/publisher-wt-integration/dashboard/publisher.js:971).

Reprodução isolada: o teste compila o AST do método real sem importar/inicializar o servidor operacional. Com SQLite temporário, `review` chama `PublisherAPI.get`; `consumption-evaluation` não chama o PublisherAPI e termina em `send_error(404)`. O controle de referência passou e a expectativa de encaminhamento da nova rota falhou. Não houve acesso a contas, credenciais ou banco real.

No Chrome Stable, o registro local de quatro publicações passou e o quinto vídeo permaneceu abandonado. Ao clicar em avaliar consumo, apareceu a mensagem HTTP 404 e o painel de consumo não abriu. Portanto não foi possível validar pela interface, nesse cenário final, a autorização destrutiva independente e os resultados parciais. Esses comportamentos passaram nos testes de domínio e nos testes browser da suíte B com fixture de respostas, mas esses fixtures não exercitam o dispatcher externo. A reprodução do dispatcher e o smoke final mostram a lacuna dessa cobertura.

Evidências: [reprodução do dispatcher](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/final-integration-gate/route-proof-evidence.json), [resultado do teste de rota](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/final-integration-gate/gate-D-route-proof-result.json), [captura do erro no Chrome](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/final-integration-gate/test_03_four_publications_one_abandoned_separate_delete_authorization_and_partial_results.png).

## Gates e resultados completos

| Gate | Resultado | Evidência |
|---|---|---|
| A - P0 obrigatório | 25 casos efetivos aprovados; zero falhas/erros de teste | gate-A-consolidated.json |
| B - primeira suíte completa | 779 executados: 775 aprovados, 2 skips, 1 falha e 1 erro de harness | gate-B-result.json |
| B - revalidação focada da rodada 1 | 15/15 aprovados | gate-B-focused1-result.json |
| B - suíte completa após rodada 1 | 779 executados: 777 aprovados, 2 skips, zero falhas/erros; execução completa | gate-B-full1-result.json |
| C - complementares | 27/27 aprovados; zero skips/falhas/erros | gate-C-result.json |
| D - primeira execução dos quatro cenários | 4 erros de fixture, antes de concluir os cenários | gate-D-result.json |
| D - última rodada autorizada | 4 executados: 2 aprovados, 1 falha de expectativa do fixture e 1 erro causado pelo endpoint ausente; 6 exceções de fixture | gate-D-final2-result.json |
| D - prova adicional de roteamento | Falha funcional reproduzida: rota de avaliação retorna 404 | route-proof-evidence.json |
| E - comandos estáticos | Python compile, node --check e ambos git diff --check aprovados | gate-E-integrity.json |
| E - integridade operacional | Não aprovada: hash do banco operacional divergiu do preflight; causa não determinada | gate-E-integrity.json |

O Gate A executou o módulo integrado e os guards de mutação/lock. Houve correção do seletor do teste de lock (`HistoricalMediaGuardTests` para `MediaSafetyConcurrencyTests`), sem alteração de teste ou asserção. O erro inicial de carregamento está preservado nos logs; não foi contado como aprovação.

A descoberta do Gate B usa `unittest.defaultTestLoader.discover` com `tests`, padrão `test_publisher*.py` e diretório raiz da integração: o mesmo escopo da descoberta solicitada, dentro dos guards reais de isolamento. Foram 779 execuções e 756 IDs distintos, devido a classes importadas já presentes na descoberta. Nenhum caso foi removido. A rodada completa aprovada terminou em 2039,509 segundos, sem fail-fast para problemas comuns. O runner interrompe em violação de isolamento, requisição externa ou falha P0.

Dois skips operacionais conhecidos foram mantidos:

- `tests.test_publisher_queue_browser.QueueBrowserTests.test_operational_snapshot_read_only`: snapshot SQLite operacional exige opt-in.
- `tests.test_publisher_thumbnails_operational.OperationalThumbnailTests.test_four_real_items_in_temporary_chrome`: thumbnail real do Drive exige opt-in.

O Gate B aprovou 1198 conexões SQLite temporárias, sem conexão bloqueada, requisição externa de browser ou violação de segurança. Exceções inesperadas do fixture: zero. Uma exceção intencional `ValueError: Fixture handler failure` pertence ao teste negativo de fechamento de SQLite. Sua classificação exige ID de teste, classes do servidor/handler, flag e exceção exatos; o log original e todas as asserções do teste foram preservados. Não há supressão genérica de exceções.

O Gate C executou exatamente `tests.test_drive_inventory` (22) e `tests.test_drive_profiles_and_assets` (5): 27 conexões SQLite temporárias, nenhuma violação de isolamento. Analytics não foi executado como dependência.

## Estabilização do harness: limite encerrado

**Rodada 1:** dois problemas da primeira execução completa foram comprovados como harness:

- `test_publisher_functional_closure_browser.py`: seletor de submit passou a identificar o formulário `batchPostingForm`, pois o modal integrado tem também o formulário independente de consumo.
- `test_publisher_library_groups_browser.py`: a espera pelo evento do servidor processa o dispatcher síncrono do Playwright. O evento e o limite de 12 segundos permanecem; continuam as verificações de identidade do elemento, conexão ao DOM, foco e valor.

Os dois arquivos conservaram as 25 e 52 chamadas de asserção, respectivamente. A revalidação focada passou em 15 casos e a nova descoberta completa passou até o final. A classificação restrita da exceção negativa foi ajustada somente no runner de QA.

**Rodada 2:** somente o fixture ignorado de smoke foi corrigido. Ele chamava `PublisherAPI.get(handler, query, path)` com assinatura incorreta, enviando o caminho no argumento de query e omitindo path. A revalidação usa o AST do dispatcher real, sem inicializar o servidor operacional, e preserva todas as asserções. Os quatro casos chegaram ao fim.

Problemas de harness remanescentes, separados do defeito funcional:

- O cenário histórico exigia checkbox desabilitado na Library. O contrato atual mantém o checkbox selecionável e aplica o bloqueio no preflight/domínio; o template só desabilita mídia `REMOTE_UNAVAILABLE`. A captura mostra o aviso histórico correto na mídia reservada. Essa falha de expectativa não comprova bypass de segurança. A asserção não foi enfraquecida ou alterada para obter aprovação.
- Seis exceções `DRIVE_SYNC_SCOPE_INCOMPLETE` vêm da configuração sintética incompleta de Drive Sync no smoke. Permanecem registradas como inesperadas; não foram ocultadas ou convertidas em sucesso.

Não houve terceira rodada. O runner/smoke/reprodução ficam em `data/publisher/qa/final-integration-gate`, ignorados pelo Git. Nenhuma mudança funcional foi realizada nesta validação.

## Smoke de UI: cobertura e limites

Chrome Stable real, quatro cenários, SQLite e perfis temporários, servidores locais com portas próprias. Nenhum servidor de teste usou 8765.

| Cenário | Resultado final |
|---|---|
| Histórico remoto inseguro | Aviso visível na captura. Caso completo não aprovado por expectativa incorreta do checkbox; as proteções de backend foram aprovadas nos Gates A/B. |
| Falha comprovadamente local | Aprovado: dispensar confirmação não envia cancelamento; aceitar envia uma única requisição, libera somente a reserva segura, arquiva o Draft, mantém snapshot/log e torna a mídia selecionável. Sem exclusão Fake Drive. |
| Quatro publicados + um abandonado | Registro de quatro publicações aprovado, abandonado preservado, sem exclusão Fake Drive; avaliação recebe 404 e impede completar a operação. Sem declaração falsa de sucesso global. |
| Interface integrada | Aprovado: modal de resolução e formulário de consumo presentes, aviso de segurança, ocultar/restaurar lote, navegação de ocultos, estados e histórico mantidos, exatamente duas requisições de apresentação, sem IDs duplicados ou erros JavaScript nos casos aprovados. |

Não se declara ausência global de ações sem backend: a rota ausente é precisamente o bloqueador encontrado. Os screenshots dos quatro cenários estão preservados no diretório de QA.

## Integridade e proteções operacionais

Branch `feature/publisher-p0-integration`, HEAD-base `29d72b1459b0dd110e0cb68919b87dd795f78b63`, sem commit. Os 25 arquivos preparados no início mantêm os SHA256 de working tree e índice; isso inclui todos os arquivos de produção e frontend. Os testes e documentos originais de Media Safety e Batch Consumption são iguais aos commits de origem após normalização de CRLF. Os comportamentos de domínio preparados passaram nas suítes; o fluxo completo de consumo na UI fica bloqueado pela rota ausente.

Os indicadores de commits de origem presentes abaixo significam conteúdo preparado na integração; não indicam que os commits estão na ancestralidade do HEAD-base.

Worktrees de origem e original: Git limpo, branches e HEADs idênticos ao preflight. Media Safety continua em `f304dfa951b265e65fba458028a66176124d9d50`; Batch Consumption em `d4aa73611cb71f6c80d25ad4386edc74e218c6f0`; operacional em `29d72b1459b0dd110e0cb68919b87dd795f78b63`. Main continua em `9f4671b28b6678da58a69bb25765314f6d6adadd`. Nenhum conflito unmerged permanece. Nenhuma alteração de Analytics, credencial, checkpoint operacional ou artifact real foi incluída no índice.

Python compile aprovou 47 arquivos (Publisher, testes corrigidos e scripts de QA); JavaScript passou em `node --check dashboard/publisher.js`. Diferenças staged/unstaged passaram em `git diff --check`. As únicas alterações desta tarefa para versionamento são os dois testes de harness e este relatório. Não houve cherry-pick --continue, reset, clean, stash, merge, push, tag ou alteração de produção.

**Divergência de banco operacional:** a verificação leu bytes/metadados do arquivo, sem abrir SQLite operacional. Tamanho manteve 34492416 bytes; SHA256 passou de `e4a23372370da36e38ea025bc7a43c64127c54e363f098128b9f65c1190cc1af` para `f30f8ee5db00933e2fd184e70d5cd625b09a2accab9fd9ca6882fbbdfb5f8877`. Havia listener local 8765/PID 20500 na inspeção somente de metadados, mas isso não determina a causa da mudança. Não se atribui a alteração a um processo sem evidência. Não se consultaram registros reais nem se alterou/reiniciou esse serviço.

`OPERATIONAL_DB_WRITES=0` e os demais contadores de ações abaixo se referem a esta tarefa. Os runners instalaram guards SQLite e de rede e reportaram somente conexões temporárias. O estado Git original está intacto; a invariância dos bytes do banco original não pôde ser confirmada. Por isso `ORIGINAL_WORKTREE_UNCHANGED=false` e o Gate E completo não é aprovado, apesar dos comandos estáticos e hashes dos arquivos preparados aprovados.

## Relatório solicitado

```text
PUBLISHER_ONLY_SCOPE=true
INTEGRATION_BRANCH=feature/publisher-p0-integration
INTEGRATION_HEAD=29d72b1459b0dd110e0cb68919b87dd795f78b63
INTEGRATED_CHANGES_PRESERVED=true
UNMERGED_FILES_REMAINING=0
MEDIA_SAFETY_SOURCE_COMMIT_PRESENT=true
BATCH_CONSUMPTION_SOURCE_COMMIT_PRESENT=true
P0_ADDITIONAL_FIX_PRESENT=true
GATE_A_P0_PASSED=true
HISTORICAL_RISK_BLOCKED=true
FAKE_DRIVE_DELETE_CALLS_FOR_BLOCKED_MEDIA=0
BLOCKED_MEDIA_MARKED_CONSUMED=false
HISTORICAL_EVIDENCE_PRESERVED=true
GATE_B_PUBLISHER_CORE_PASSED=true
PUBLISHER_CORE_DISCOVERED=779
PUBLISHER_CORE_DISTINCT=756
PUBLISHER_CORE_PASSED=777
PUBLISHER_CORE_SKIPPED=2
PUBLISHER_CORE_FAILED=0
PUBLISHER_CORE_ERRORS=0
PUBLISHER_CORE_UNEXPECTED_FIXTURE_ERRORS=0
PUBLISHER_CORE_EXPECTED_NEGATIVE_FIXTURE_ERRORS=1
GATE_C_COMPLEMENTARY_PASSED=true
COMPLEMENTARY_TEST_COUNT=27
COMPLEMENTARY_TESTS_PASSED=27
GATE_D_UI_SMOKE_PASSED=false
UI_SMOKE_DISCOVERED=4
UI_SMOKE_PASSED=2
UI_SMOKE_FAILED=1
UI_SMOKE_ERRORS=1
UI_SMOKE_UNEXPECTED_FIXTURE_ERRORS=6
MEDIA_SAFETY_UI_VALIDATED=false
LOCAL_FAILURE_RECOVERY_UI_VALIDATED=true
BATCH_CONSUMPTION_UI_VALIDATED=false
HTML_CONFLICT_RESOLUTION_VALIDATED=true
GATE_E_STATIC_PASSED=false
STATIC_COMMANDS_PASSED=true
COMPILE_CHECK_PASSED=true
JS_SYNTAX_CHECK_PASSED=true
GIT_DIFF_CHECK_PASSED=true
PREPARED_FILE_HASHES_UNCHANGED=true
OPERATIONAL_DB_HASH_UNCHANGED=false
OPERATIONAL_DB_DRIFT_CAUSE=UNDETERMINED
HARNESS_FIXES_APPLIED=true
HARNESS_STABILIZATION_ROUNDS=2
HARNESS_ONLY_CHANGES=true
PRODUCTION_DEFECTS_FOUND=1
OPERATIONAL_DB_WRITES=0
TIKTOK_REMOTE_ACTIONS=0
DRIVE_REMOTE_WRITES=0
REAL_DRIVE_DELETIONS=0
ANALYTICS_CODE_CHANGED=false
ORIGINAL_WORKTREE_UNCHANGED=false
ORIGINAL_TRACKED_WORKTREE_UNCHANGED=true
SOURCE_WORKTREES_UNCHANGED=true
COMMIT_CREATED=false
OPERATIONAL_BRANCH_UNCHANGED=true
MAIN_UNCHANGED=true
REMOTE_PUSH_PERFORMED=false
TECHNICAL_INTEGRATION_VALIDATION_PASSED=false
FINAL_DECISION=BLOCKED_BY_PRODUCT_DEFECT
READY_FOR_SEPARATE_INTEGRATION_COMMIT=false
```

Os resultados JSON e logs de cada gate, manifests do preflight, reprodução de rota e auditoria final ficam em [QA final](C:/Users/Usuario/Documents/publisher-wt-integration/data/publisher/qa/final-integration-gate/gate-E-integrity.json). Os relatórios anteriores foram preservados como histórico. Trabalho encerrado após este relatório, sem autorização presumida para correção de produção ou implantação.
