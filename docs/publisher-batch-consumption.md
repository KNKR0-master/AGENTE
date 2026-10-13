# Finalização guiada de consumo dos lotes

Implementação isolada baseada em `29d72b1459b0dd110e0cb68919b87dd795f78b63`.
Este documento descreve código preparado; validação funcional ainda pendente.
Nenhum servidor, migration, teste funcional, gate, banco operacional ou adapter
real foi executado durante esta implementação.

## Fluxo

Na revisão de lote, o registro manual continua sendo uma declaração local.
Depois dele, a interface oferece avaliação de consumo. A avaliação consulta
metadata do Drive sem criar intenção de exclusão e apresenta elegíveis, já
consumidas, bloqueadas, abandonadas e solicitações pendentes.

A seleção mostra nomes, contas, Drive IDs e quantidade. Um formulário separado
exige autorização explícita da exclusão permanente no Drive. Alterar a seleção
desmarca essa autorização. Ocultar o lote continua sendo apresentação; cancelar
continua sendo uma operação local de liberação de reservas.

## Contratos

- `GET /api/publisher/batches/{id}/review`: revisão local e resumo de consumo.
- `GET /api/publisher/batches/{id}/consumption-evaluation`: leitura remota de
  identidade de candidatos publicados; nenhuma escrita ou exclusão.
- `POST /api/publisher/batches/{id}/finalize-consumption`: requer
  `drive_delete_confirmed: true`, `confirmed_count`, `request_id` e `items`
  contendo `draft_id` e o `identity_token` recebido na avaliação.

A finalização passa por `cleanup_remote`, sem endpoint de exclusão direta.
Cada identidade inclui Batch, Draft, MediaAsset, conta Publisher, origem Drive,
nome, tamanho, checksum e checkpoints persistidos. A identidade e a elegibilidade
são verificadas novamente dentro das transações do pipeline antes do efeito remoto.
Metadata remota valida ID, nome, tamanho, checksum, tipo e pasta; o adapter real
revalida a árvore de origem permitida imediatamente antes da exclusão.

## Persistência, concorrência e resultados

Não foram adicionadas tabelas ou migrations. A autorização, os estados por item
e os recibos usam o log de auditoria existente, sem modificar histórico de
execuções. Eventos: `BATCH_CONSUMPTION_REQUEST`, `BATCH_CONSUMPTION_ITEM` e
`BATCH_REMOTE_DELETE_ACK`; a limpeza local confirmada usa
`LOCAL_CLEANUP_CONFIRMED`.

Requests são serializados no processo. As autorizações e claims por item usam
`BEGIN IMMEDIATE`; o pipeline existente serializa também a exclusão remota com
SQLite. O request ID impede reaplicar uma solicitação concluída. Outra solicitação
não apropria itens já pendentes. Em vários processos, o claim persistido impede
que a mesma solicitação execute o mesmo item simultaneamente.

`COMPLETE`, `PARTIAL` e `PENDING` distinguem conclusão, falhas/bloqueios e trabalho
pendente. Há resultado por mídia e indicador de limpeza local pendente mesmo
quando o checkpoint remoto já é `CONSUMED`. A operação individual permanece
disponível para exceções. A Queue apresenta consumo pendente, concluído, bloqueado
e abandono preservado separadamente do estado de publicação.

A ausência remota só é reconciliada neste fluxo após resposta afirmativa da
exclusão, vinculada à mesma identidade. Uma intenção antiga, um 404 inicial,
arquivo em lixeira ou identidade divergente não bastam. Uma falha local depois
da resposta do Drive conserva o recibo e permite reconciliar sem repetir delete.
Uma interrupção antes de persistir o recibo deixa o item `RUNNING`, exige
reconciliação humana e não repete automaticamente a exclusão. Se o checkpoint
`CONSUMED` já estiver persistido com a mesma identidade, a mesma solicitação pode
concluir apenas a limpeza local, sem sequer criar um adapter de Drive. Reenvios
do mesmo request consultam os resultados; itens ainda `QUEUED` podem continuar.

O processamento HTTP é síncrono e sequencial por lote. Pode durar várias chamadas
de Drive. A página mantém o request ID após erro de rede para consultar a mesma
solicitação. Fechar/reabrir a revisão ainda mostra pendências persistidas. O
isolamento de timeouts e a experiência sob latência real devem ser avaliados na
validação separada.

## Validação preparada, não executada

`tests/test_publisher_batch_consumption.py` prepara regressões em SQLite
descartável e com `FakeIdentityDrive`, sem adapters reais. Inclui quatro
publicados e um abandonado, todos publicados, nenhum elegível, consumo parcial,
idempotência, falha de Drive, transferência ambígua, identidade local/remota
divergente, concorrência, autorização separada, lote oculto, cancelamento local,
404, lixeira, recibo e falha de persistência, limpeza local pendente, avaliação
somente leitura e contrato HTTP.

`tests/test_publisher_batch_consumption_browser.py` prepara o fluxo guiado com
servidor HTTP falso e navegador em perfil descartável. Nenhum acesso remoto é
permitido pela fixture. A execução é opt-in para uma validação posterior.

Verificações desta implementação são apenas parsing/compilação estática Python,
`node --check`, integridade dos IDs usados na UI e `git diff --check`.

## Arquivos alterados

- `dashboard/publisher.html`
- `dashboard/publisher.js`
- `src/publisher/batch_consumption.py`
- `src/publisher/http.py`
- `src/publisher/lifecycle.py`
- `src/publisher/media.py`
- `src/publisher/posting_review.py`
- `src/publisher/service.py`
- `tests/test_publisher_batch_consumption.py`
- `tests/test_publisher_batch_consumption_browser.py`
- `docs/publisher-batch-consumption.md`

Total: 11 arquivos. Foram preparadas 26 regressões (25 de domínio/HTTP e uma
de UI), não executadas. A worktree original permaneceu limpa, com HEAD
`29d72b1459b0dd110e0cb68919b87dd795f78b63`, e `main` permaneceu em
`9f4671b28b6678da58a69bb25765314f6d6adadd`. Nenhum commit foi criado.
