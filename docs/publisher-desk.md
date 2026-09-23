# Publisher Desk: estado estavel e operacao

Checkpoint normativo: 15/09/2026. Este documento descreve o comportamento atual
validado do Publisher Desk. Relatorios em `docs/publisher-*` com datas anteriores
sao evidencias historicas e nao substituem este fluxo.

## Arquitetura

O Publisher Desk roda no mesmo `dashboard_server.py`, usa `data/agent.db` e
possui frontend em `dashboard/publisher.html`, `publisher.js` e `publisher.css`.
O worker normal e `src/publisher/worker.py`; a camada de runtime do navegador e
`src/publisher/browser_runtime.py`; ownership e mutex ficam em
`src/publisher/manual_profile.py`.

O banco continua sendo a fonte de verdade para contas, produtos, drafts,
MediaAssets, BatchExecutions, itens de execucao e logs. Analytics, afiliados,
estoque e a fila manual de postagem permanecem modulos separados.

## Isolamento operacional do Analytics

O pacote `src/publisher` nao importa `src.affiliate`, `src.affiliate_analytics`,
`src.tiktok_analytics` nem depende de `content_performance` para iniciar,
autenticar, consultar o Drive ou executar um BatchExecution. O Publisher pode
operar enquanto os importadores e calculos de Analytics estiverem incompletos
ou em evolucao.

`dashboard_server.py` compartilha a inicializacao do processo e roteia as duas
areas, mas isso e apenas infraestrutura de entrada. Nenhuma rota ou calculo de
Analytics e pre-requisito funcional do runtime, da autenticacao, do batch ou do
provider de Drive do Publisher.

## UI 2A.1: catalogo, preferencias e pastas

A primeira entrega da nova UI permanece limitada a Produtos, health e
preferencias. A migracao e incremental e idempotente: `publisher_products`
recebe `flow_prompt` sem recriar ou renumerar produtos, e
`publisher_preferences` guarda `default_publisher_account_id` por referencia
de banco, nunca apenas por nome. Se a conta padrao for removida ou ficar
inativa, a tela de criacao volta a exigir selecao normal.

O campo `flow_prompt` e cadastro editorial multilinha. Ele ainda nao participa
do worker, do upload ou de qualquer automacao TikTok. A associacao de produtos
e pastas usa exclusivamente `drive_folders` e
`drive_folder_mapping_history`; um produto pode ter varias pastas, cada pasta
tem no maximo um produto canonico e associar/remover nao move arquivos.

`drive_files` e o inventario completo para a futura Biblioteca. `MediaAsset` e
somente o subconjunto operacional elegivel para o lifecycle de drafts. A UI
2A.1 nao implementa Changes API, polling em background, classificacao nova de
midia, publicacao ou agendamento.

Exclusao em massa de produtos e protegida por referencias historicas. Produtos
sem referencias podem ser excluidos apos confirmacao visual; produtos com
drafts, batches, metricas, conteudo, pastas ou outros historicos sao arquivados
e nunca apagados fisicamente. Uma selecao mista mostra os dois totais antes da
confirmacao.

## UI 2A.2: Biblioteca e lotes multi-produto

A aba `Biblioteca` usa `drive_files` como fonte de inventario completo e
aplica filtros combinaveis no servidor por nome/ID, caminho, produto, tipo,
status e decisao. A paginacao e limitada a 100 itens por consulta. A exibicao
usa estados amigaveis (`DISPONIVEL`, `RESERVADO`, `REQUER ATENCAO` e
`IGNORADO`), mantendo a classificacao tecnica internamente.

A classificacao automatica e deliberadamente conservadora: `G<n>C<n>A<n>` e
`FINAL` sao alta confianca; `VIDEO <n>` e media confianca somente quando o
produto pode ser resolvido pela arvore de pastas e os metadados de video sao
validos. Nomes suspeitos ficam em atencao ate uma decisao manual `VALID` ou
`IGNORED`. A associacao de arquivo a produto pode usar override persistente ou
o mapeamento de pasta existente, sem mover arquivos no Drive.

`Criar lote` aceita varias midias com produtos diferentes. O preflight valida
toda a selecao antes de abrir transacao; qualquer `PRODUCT_UNASSIGNED`,
`CAPTION_MISSING`, conflito de conta, midia reservada ou metadado invalido
aborta a operacao inteira. Quando aprovado, cria apenas referencias
`MediaAsset`/`Draft` e reserva as midias: nao baixa, nao abre o TikTok e nao
inicia cleanup remoto. O download JIT continua pertencendo ao worker normal.

A acao `Excluir do Drive` e separada da criacao de lote, exige confirmacao
humana no navegador e no backend, compara a identidade do arquivo e confirma
que o ID deixou de existir pela mesma integracao antes de atualizar o
inventario local. Ela nao pode ser usada para midia vinculada a draft; nesse
caso o lifecycle do Publisher continua sendo a unica rota de cleanup.

A selecao da Biblioteca tem uma unica fonte de verdade: o `Set`
`librarySelected`, com os checkboxes identificados por `data-library` e
alterados por um listener central. A selecao individual, a selecao da pagina,
a remocao individual e a limpeza total atualizam o mesmo conjunto; isso tambem
permite resumir uma selecao que atravessa paginas e produtos sem exigir um
produto unico por lote. Erros de health, consulta da Biblioteca e outras
operacoes usam escopos separados: uma nova leitura bem-sucedida remove apenas
o erro daquela operacao, preservando alertas de outras areas.

O inventario historico contem 2054 registros, dos quais 1759 tem extensao
`.mp4`. A Biblioteca operacional atual consulta somente arquivos nao marcados
como ausentes (`missing_since IS NULL`) em pastas com
`operational_status='ACTIVE'`; essa interseccao contem 748 arquivos de todos
os tipos e 515 videos. Os demais registros sao historico ausente (1192) ou
estao em pastas fora do escopo operacional atual (114, incluindo pastas
arquivadas, de reserva ou de sistema). Portanto, 515/748 e uma visao
deliberadamente operacional, nao uma tentativa de fazer o inventario bruto
igualar a Biblioteca. A sincronizacao futura pode reconciliar ausencias, mas
nao deve ampliar o escopo silenciosamente.

As tabelas `publisher_media_decisions` e `publisher_media_overrides` guardam
as decisoes e atribuicoes sem apagar o inventario original. Nao ha Changes API,
polling em background, sincronizacao automatica ou classificacao baseada em
IA nesta fase.

## Runtime de producao

O caminho validado nao usa `launch_persistent_context()` para o Publisher.
O fluxo atual e:

```text
BrowserRuntimeManager
  -> subprocess.Popen(args, shell=False)
  -> Chrome instalado com userDataDir persistente
  -> CDP somente em 127.0.0.1
  -> playwright.chromium.connect_over_cdp()
  -> BrowserContext existente
  -> verificacao read-only da sessao
  -> worker
```

O mesmo perfil persistente da conta `CECILIA FIGUEIREDO` e reutilizado. O
`ProfileOwnership` mantem mutex por caminho, registra browser ownership,
impede dois runtimes concorrentes e reconcilia leases depois de encerramento ou
crash. O fechamento normal usa `CloseMainWindow`; locks nativos do Chrome e
cookies/storage nao sao removidos pelo Publisher.

O fluxo de autenticacao e manual. O detector combina URL do Studio, cookie de
sessao, shell/DOM autenticado e ausencia de UI de login. A identidade e uma
camada separada: o handle esperado e `cecilia.figueired65`, normalizado sem
aceitar outro username. Uma sessao persistida foi validada atraves de:

```text
fechar Chrome e Publisher
-> abrir novo Chrome pelo BrowserRuntimeManager
-> attach CDP em novo processo
-> Studio sem redirect de login
-> AUTH_RESULT=AUTHENTICATED
-> SESSION_VERIFIED=true
-> ACCOUNT_IDENTITY=ACCOUNT_CONFIRMED
```

O TikTok pode invalidar a sessao por decisao propria. Nesse caso, o login e
manual; QR, senha, 2FA e challenge nao sao automatizados. O worker fica
bloqueado ate nova verificacao positiva da sessao e da identidade.

## Fonte de midia e ciclo validado

A origem operacional e o Remote Media Inbox do Google Drive da Cecilia. O
provider usa a identidade OAuth de usuario da conta
`ceciliafigueiredo.shop@gmail.com`, com o unico scope:

```text
https://www.googleapis.com/auth/drive
```

O token/refresh token fica protegido por Windows DPAPI. Listagem, metadata,
download, capabilities, delete e confirmacao pos-delete usam a mesma identidade
OAuth; nao existe fallback silencioso para service account em `user_oauth`.

O fluxo validado por draft e:

```text
Remote Media / Google Drive
  -> MediaAsset AVAILABLE
  -> Draft
  -> BatchExecution
  -> JIT download
  -> preflight ffprobe
  -> upload TikTok Studio
  -> TRANSFER_CONFIRMED
  -> MEDIA_READY_FOR_EDIT
  -> aplicar Draft.caption e validar
  -> Salvar rascunho
  -> confirmar em /tiktokstudio/content
  -> READY_FOR_REVIEW / PERSISTED
  -> CONSUMED_PENDING_DELETE
  -> delete remoto
  -> confirmar 404/not found
  -> CONSUMED
  -> remover TEMP
```

`delete` so ocorre depois de `PERSISTED`. A ausencia confirmada pela API apos
`deleteRemote` e o checkpoint de exclusao. O cleanup e idempotente: se o remoto
ja estiver ausente depois de uma intencao valida, nao ha segunda chamada de
delete; se o cleanup nao puder ser reconciliado, TEMP e a origem sao preservados.

## ffprobe

O preflight local e obrigatorio e ocorre antes do download/upload quando possivel.
A resolucao e:

```text
FFPROBE_PATH explicito
  -> configuracao persistida
  -> shutil.which("ffprobe")
```

O helper usa `subprocess.run` com argumentos separados e `shell=False`, inclusive
para caminhos com espacos. Os erros diferenciados sao
`FFPROBE_NOT_FOUND`, `FFPROBE_TIMEOUT`, `FFPROBE_EXIT_ERROR` e `INVALID_MEDIA`.
Falha local nao inicia download JIT, reserva definitiva ou upload.

## Upload, persistencia e popups

`UPLOAD_STARTED` junto de timeout em `setInputFiles` nao prova falha de upload.
Se a pagina ainda estiver viva, o worker faz uma reconciliacao read-only por
filename exato, `Enviado (...)`, preview/capa e editor pronto. Enquanto houver
possivel efeito remoto, um segundo `setInputFiles` fica bloqueado.

O clique em `Salvar rascunho` tambem nao prova persistencia. A confirmacao usa
`/tiktokstudio/content` e combina legenda, checkpoint/timestamp, duracao e
thumbnail. Captions duplicadas sao permitidas; nao se exige unicidade global.

Somente estes dois dialogs sao autoaceitos:

```text
"Veja a prévia do seu vídeo no seu celular" -> "Entendi"
"Salvar rascunho?" + verificacao de direitos autorais em andamento
  -> "Salvar mesmo assim"
```

Qualquer outro popup resulta em `MANUAL_ACTION_REQUIRED`. Publicar, agendar e
vincular produto nunca sao autoaceitos.

## Batches e seguranca operacional

O processamento e sequencial (`concurrency=1`) e fail-fast. O mesmo
`BatchExecution` pode pausar, reconciliar e retomar, sem criar lote paralelo.
Protecoes impedem reupload de draft persistido e impedem processar itens seguintes
quando existe uma condicao manual inconclusiva.

O primeiro batch real validado terminou com:

```text
BATCH_RESULT=SUCCESS
BATCH_SUCCEEDED=3
BATCH_PENDING=0
REMOTE_FILES_DELETED=3
TEMP_FILES_REMAINING=0
```

## Limites atuais

```text
criacao de drafts                 VALIDADA
batch sequencial                  VALIDADO
persistencia de sessao            VALIDADA
cleanup Drive                     VALIDADO
publicacao automatica             NAO VALIDADA / DESABILITADA
agendamento automatico            NAO VALIDADO / DESABILITADO
vinculo automatico TikTok Shop    NAO IMPLEMENTADO / DESABILITADO
```

O resultado operacional e um rascunho persistente para revisao humana. O
vinculo de produto continua manual durante a revisao.

## Inicializacao e encerramento no Windows

O entrypoint oficial e `start-publisher.bat`, que localiza a raiz do projeto e
executa `scripts/start_publisher.py`. O launcher impede uma segunda instancia
quando o Publisher ja esta respondendo em `127.0.0.1:8765`.

`Ctrl+C`, `Ctrl+Break` e o fechamento da janela do console pelo X sao tratados
por uma unica coordenacao de shutdown idempotente. Ela sinaliza o servidor,
aguarda o encerramento gracioso dentro de um limite curto e nao usa `taskkill`
como caminho normal. O servidor continua responsavel por chamar
`Publisher.worker.shutdown()`, que desanexa o Playwright, fecha apenas browsers
que pertencem ao runtime, libera CDP e o mutex do profile. Browsers externos
conectados via CDP permanecem sob responsabilidade do usuario.

No `CTRL_CLOSE_EVENT`, o launcher primeiro envia uma solicitacao HTTP cooperativa
curta e entao arma o mesmo coordenador de shutdown; a rotina completa continua
fora do callback de console. Os eventos `*_RECEIVED`,
`COOPERATIVE_SERVER_SHUTDOWN_CALLED`, `CHILD_PROCESS_EXIT_OBSERVED` e
`HANDLER_RETURNED_BEFORE_CHILD_EXIT` sao registrados em
`logs/publisher-launcher.log` sem credenciais ou conteudo de requests. O
resultado de um teste real de fechamento pelo X deve ser considerado validado
somente quando servidor, Chrome owned, CDP, mutex e porta forem observados
liberados sem intervenção posterior.

Se o encerramento for interrompido pelo Windows, a inicializacao seguinte
continua usando a reconciliacao existente de mutex, profile e lifecycle; nenhum
retry de draft e iniciado pelo launcher.

## Sincronizacao da Biblioteca

`Sincronizar agora` usa `POST /api/publisher/drive-sync/incremental` sem
bootstrap. O endpoint legado `/api/publisher/sync-media` delega ao mesmo motor
para manter compatibilidade; ele nao usa mais `GoogleDriveMediaProvider.list_files`
como segunda descoberta. A Biblioteca e atualizada a partir das tabelas locais
`drive_folders`, `drive_files` e `publisher_media_assets`, preservando filtros e
selecao da tela.

Sem cursor valido, a UI pede uma `Verificacao completa do Drive` separada. Essa
acao exige confirmacao explicita e executa o bootstrap com token T0 anterior ao
scan completo e catch-up pela Changes API. Erro ou cursor invalido nunca dispara
reconstrucao automatica. Um lock por conta, provider e raiz impede ciclos
simultaneos no processo Publisher. O status consultavel nao inclui page tokens.
Essa sincronizacao nao faz escrita remota, nao cria produtos automaticamente e
nao inicia polling de Changes em background.

## Referencias historicas

- `docs/publisher-persistence-lifecycle.md`: detalhes do lifecycle e recovery.
- `docs/publisher-auth-observability.md`: contrato de observabilidade passiva.
- `docs/publisher-auth-controlled-test.md`: preparacao e experimento historicos.
- `docs/publisher-login-run-5db008ee.md`: resultado historico do run one-shot.
- `docs/cecilia-drive-user-oauth-preparation.md`: migracao OAuth e DPAPI.
- `docs/cecilia-drive-delete-permission-diagnosis.md`: diagnostico inicial da
  conta de servico, mantido como evidencia da causa original.
