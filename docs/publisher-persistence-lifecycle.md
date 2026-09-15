# Publisher: lifecycle de drafts e consumo de midia

Estado normativo validado em 15/09/2026. O worker de producao usa CDP via
`BrowserRuntimeManager`; scripts de probe e reconciliacao historica nao fazem
parte do caminho normal.

## Lifecycle normal

```text
AVAILABLE
  -> DRAFT
  -> PREPARING
  -> JIT download
  -> upload TikTok Studio
  -> TRANSFER_CONFIRMED
  -> MEDIA_READY_FOR_EDIT
  -> Draft.caption aplicada e validada
  -> SAVE_DRAFT
  -> confirmacao em /tiktokstudio/content
  -> READY_FOR_REVIEW + PERSISTED
  -> CONSUMED_PENDING_DELETE
  -> files.delete(remote_file_id)
  -> files.get() = 404/not found
  -> CONSUMED
  -> TEMP removido
```

O worker somente entra em `CONSUMED_PENDING_DELETE` depois de persistencia
confirmada. O registro do MediaAsset nao e apagado: filename, remote_file_id,
tamanho, checksum e draft consumidor permanecem no historico.

## Idempotencia e crash recovery

Antes do delete, o worker grava o checkpoint de intencao e confirma a identidade
do MediaAsset. Depois de `deleteRemote`, um `404` somente confirma exclusao quando
todos estes sinais existem:

- remote_file_id conhecido;
- identidade/checksum registrados;
- draft `PERSISTED`;
- status `CONSUMED_PENDING_DELETE`;
- checkpoint de intencao de exclusao;
- operacao associada ao mesmo MediaAsset.

Nesse contexto, `delete -> crash -> restart -> files.get=404` e reconciliado para
`CONSUMED` sem nova chamada `files.delete`. Um `404` fora desse contexto resulta
em `MANUAL_ACTION_REQUIRED`.

Se o delete falhar, o estado permanece `CONSUMED_PENDING_DELETE`, TEMP fica
preservado e nao ha retry automatico. Se `CONSUMED` ja estiver gravado e somente
TEMP faltar, a finalizacao local pode ser concluida sem tocar no Drive.

## Upload inconclusivo

Um checkpoint `UPLOAD_STARTED` seguido de timeout local em `setInputFiles` e
tratado como `TRANSFER_UNKNOWN`/reconciliacao necessaria, nunca como autorizacao
para reupload. A reconciliacao read-only exige filename exato, `Enviado (...)`,
preview/capa e editor pronto.

O clique em `Salvar rascunho` nao e suficiente. O match em `/tiktokstudio/content`
combina legenda, duracao, thumbnail e janela temporal do checkpoint. Duplicidade
de captions nao invalida o match; ambiguidade pausa o item.

## Execucao em lote

`BatchExecution` usa `concurrency=1`, preserva a ordem original e aplica fail-fast.
Um item manualmente inconclusivo pausa os seguintes. A retomada usa o mesmo
execution id e primeiro reconcilia o item pendente; nao cria um novo batch.

O primeiro batch real de tres drafts foi concluido com sucesso:

```text
BATCH_RESULT=SUCCESS
BATCH_TOTAL=3
BATCH_SUCCEEDED=3
BATCH_PENDING=0
REMOTE_FILES_DELETED=3
TEMP_FILES_REMAINING=0
```

## Protecoes

- `persistence_status=PERSISTED` bloqueia novo upload do mesmo draft.
- MediaAsset `CONSUMED` e `CONSUMED_PENDING_DELETE` nao voltam a AVAILABLE.
- cleanup exige confirmacao administrativa explicita quando disparado pela API.
- publicacao, agendamento e vinculo de produto nao fazem parte do worker automatico.
- logs nao registram credenciais, cookies, access tokens ou refresh tokens.
- TEMP fica dentro da raiz controlada do Publisher e usa lease para evitar limpeza
  durante um job ativo.

## Operacao e referencias

O endpoint de cleanup existe para a finalizacao administrativa de um draft
persistido. A rotina normal nao inicia cleanup durante migracao ou startup.
Detalhes de autenticacao, mutex e CDP estao em `docs/publisher-desk.md` e no
checkpoint `docs/publisher-stable-checkpoint.md`.

Scripts como `consolidate_publisher_checkpoint.py` e `draft1_preflight.py` sao
artefatos historicos de auditoria. O worker de producao nao os importa nem
depende deles.
