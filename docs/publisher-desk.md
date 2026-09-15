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

## Referencias historicas

- `docs/publisher-persistence-lifecycle.md`: detalhes do lifecycle e recovery.
- `docs/publisher-auth-observability.md`: contrato de observabilidade passiva.
- `docs/publisher-auth-controlled-test.md`: preparacao e experimento historicos.
- `docs/publisher-login-run-5db008ee.md`: resultado historico do run one-shot.
- `docs/cecilia-drive-user-oauth-preparation.md`: migracao OAuth e DPAPI.
- `docs/cecilia-drive-delete-permission-diagnosis.md`: diagnostico inicial da
  conta de servico, mantido como evidencia da causa original.
