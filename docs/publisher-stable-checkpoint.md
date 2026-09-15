# Publisher Draft Pipeline: stable checkpoint

Status: **STABLE / VALIDATED**
Data: 15/09/2026
Proxima fase: **Publisher UI/UX**

## Componentes validados

- Publisher Desk local com SQLite e BatchExecution sequencial.
- BrowserRuntimeManager com Chrome persistente, `Popen(args, shell=False)` e
  Playwright `connect_over_cdp()` somente em `127.0.0.1`.
- Mutex de profile, ownership, shutdown gracioso e reconciliacao de runtime
  stale.
- Sessao TikTok persistente da conta `cecilia.figueired65`.
- Verificacao separada de autenticacao e identidade da conta.
- Google Drive da Cecilia via `user_oauth`, conta
  `ceciliafigueiredo.shop@gmail.com`, scope `https://www.googleapis.com/auth/drive`
  e armazenamento DPAPI.
- Preflight ffprobe com resolucao explicita, configuracao persistida e PATH.
- Upload JIT, reconciliacao de timeout, persistencia em Rascunhos e protecao
  contra reupload.
- Cleanup remoto idempotente com confirmacao 404/not found e remocao posterior
  de TEMP.
- Batches `concurrency=1`, fail-fast, pausa, reconciliacao e retomada do mesmo
  BatchExecution.

## Fluxo oficial

```text
Remote Media -> AVAILABLE -> Draft -> BatchExecution -> ffprobe
-> JIT download -> upload -> TRANSFER_CONFIRMED -> MEDIA_READY_FOR_EDIT
-> Draft.caption -> PERSISTED em Rascunhos
-> CONSUMED_PENDING_DELETE -> delete remoto -> 404/not found
-> CONSUMED -> TEMP removido
```

Somente os popups allowlisted de preview no celular e de verificacao de direitos
autorais com `Salvar mesmo assim` podem ser aceitos automaticamente. Qualquer
outro dialog pausa para acao manual.

## Capacidades validadas

```text
criacao de drafts              VALIDADA
batch sequencial               VALIDADO
persistencia de sessao         VALIDADA
Drive OAuth e cleanup          VALIDADOS
reconciliacao 404              VALIDADA
```

## Fora do escopo atual

```text
publicacao automatica          NAO VALIDADA / DESABILITADA
agendamento automatico         NAO VALIDADO / DESABILITADO
vinculo automatico de produto  NAO IMPLEMENTADO / DESABILITADO
TikTok Video Analytics oficial AGUARDANDO RELATORIO EXPORTAVEL
```

O Publisher deixa rascunhos para revisao humana. Produto, publicacao e
agendamento continuam manuais.

## Seguranca e dados locais

Perfis Chrome, cookies, storage, TEMP, SQLite operacional, DPAPI, OAuth client,
tokens e evidencias de runtime ficam fora do Git. A documentacao nao inclui
client secret, access token, refresh token ou conteudo DPAPI.

O TikTok pode invalidar a sessao. Login, QR, senha, 2FA e challenge permanecem
manuais; o worker nao inicia reautenticacao silenciosa e exige nova verificacao
positiva da conta antes de operar.

## Divida tecnica preservada

- O caminho `launch_persistent_context()` permanece apenas como legado/teste e
  nao deve voltar ao Publisher de producao sem nova validacao.
- A interface ainda precisa de uma fase propria de UX; esta etapa nao altera UI.
- A API oficial TikTok Shop Creator continua desabilitada para BR.
- A ausencia de Video Analytics exportavel impede metricas de eficiencia por view.
