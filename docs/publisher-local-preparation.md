# Publisher — preparação local restrita

Base: `069fc20c08c398ae846b13c0da470cf38a40dad2`, na worktree de integração.
Implementação sem commit ou implantação. O gate Publisher completo permanece
pendente e a ausência de prova real do Studio continua limitando o fluxo remoto.

## Uso

Na Biblioteca, selecionar mídias elegíveis e escolher **Preparar vídeo localmente**.
Cada abertura começa com headline desligada em todos os vídeos, mesmo quando o
Product tem uma sugestão salva (`DEFAULT_PER_VIDEO_HEADLINE_ENABLED=false`).
Selecionar individualmente texto salvo, personalizado ou sugestão do Product,
estilo e tamanho. A configuração bulk só é aplicada por uma ação explícita.
Revisar cada mídia antes de iniciar somente a preparação local.

O painel **Preparações locais — sem envio ao TikTok** mostra etapas reais e seus
tempos, estado, identidade, headline aplicada, duração, resolução e data. O
resultado permite abrir prévia e obter o MP4. Não existe transição desta tarefa
para upload, Save Draft, agendamento, publicação ou consumo. Download não concede
autorização futura para nenhuma dessas ações.

## Entidade, isolamento e concorrência

`publisher_local_preparations` é uma migration aditiva no catálogo Publisher.
Nesta entrega ela só é executada nas bases sintéticas dos testes. Nenhuma
migration ou inicialização foi executada contra `data/agent.db` operacional.

As tarefas usam UUIDs locais, não IDs de Drafts remotos. Identidade, request,
snapshot e nome são imutáveis por trigger. O token de revisão liga o início à
identidade e configuração revisadas. Repetição do mesmo request não duplica jobs.
Um índice parcial impede tarefas simultâneas sobre a mesma identidade. O guard
de admissão remota também rejeita mídia com preparação local ativa, inclusive
quando ainda não existe MediaAsset. Não se altera status/reserva do MediaAsset.

Há um coordenador local serial e um lease de arquivo exclusivo por catálogo.
O worker do TikTok e BrowserRuntimeManager não são usados pelo pipeline local.
A leitura de origem usa somente metadata/download do adapter Drive existente;
cancelamento é conferido também entre chunks do download. Não há chamadas a
cleanup, consumo, OAuth TikTok, transferência ou persistência remota.

O histórico P0 é verificado na revisão, no claim, na repetição e na conclusão.
ABANDON/ARCHIVE não removem risco histórico. Reservas de publicação existentes
bloqueiam a preparação local e nunca são liberadas por ela.

## MP4, identidade e cache

O renderer existente permanece inalterado: libx264 medium, CRF 18, até quatro
threads efetivas e encode limitado a 600 segundos. Headline habilitada exige
derivado validado, sem fallback silencioso. CFR/VFR, contagem/apresentação dos
frames, áudio, duração e timestamps conservam as validações existentes.

Sem headline, a origem é validada por metadata, decode completo e timeline e
copiada sem reencodificação; o resultado é bit a bit idêntico à origem. Fontes
e configuração de headline não são exigidas para esse caminho.

Os arquivos ficam em `TEMP/PublisherDesk/local_preparation/<escopo-do-catalogo>`.
Cada UUID possui inputs, catálogo SQLite próprio do renderer, artifacts e MP4.
O catálogo do renderer é independente do catálogo/artifacts dos Drafts remotos.
O cache de uma tentativa é validado pelo renderer e pode ser reutilizado numa
repetição do mesmo job, com source hash, render-spec hash e parâmetros efetivos
compatíveis. Jobs novos têm histórico e arquivos próprios; não há deduplicação
global entre identidades. Snapshot/identidade diferentes exigem nova preparação.

## Falhas, retomada e retenção

Cancelamento é cooperativo. Falha ou cancelamento não disponibiliza MP4 nem
marca mídia consumida, persistida ou publicada. A repetição é exclusivamente
local, conserva o snapshot e revalida a identidade. Alteração de texto/preset ou
da identidade exige nova revisão e nova tarefa.

Jobs interrompidos são marcados como falhos pelo novo proprietário exclusivo;
nunca são recolocados automaticamente em processamento ou no pipeline remoto.
Essa recuperação também é feita na inicialização do Publisher, para retirar
claims locais interrompidos sem alterar reservas de publicação.
Atualização da página só consulta estados e resultados.

O MP4 fica disponível por sete dias após conclusão. Expiração impede servir o
arquivo; não implica exclusão automática do Drive nem da origem. A remoção de
arquivos locais exige confirmação explícita e só alcança o diretório UUID da
tarefa, inclusive seu cache e origem materializada. O registro e snapshot são
preservados. A árvore local tem limite de admissão de 16 GiB, com orçamento de
quatro vezes o tamanho de origem para uma nova preparação; mídia individual
limitada a 2 GiB. O usuário remove resultados locais para liberar espaço.

Endpoints aceitam IDs locais e nunca caminhos. Prévia/download exigem estado
READY, validade e SHA-256 correspondente ao resultado; suportam byte ranges,
no-store e nome de download controlado. Remoção de arquivo local e exclusão no
Drive são operações distintas.

## Validação

O runner `python -B -m tests.publisher_local_preparation_gate` seleciona somente
os módulos direcionados declarados nele. Usa SQLite em QA/TEMP/memória, mídia
sintética, Fake Drive e Chrome Stable descartável, com páginas limitadas ao
servidor loopback de fixture. Conexões SQLite operacionais, rede externa e porta
8765 são proibidas pelo harness. As worktrees protegidas e os hashes executáveis
são comparados antes/depois. Evidências ficam em
`data/publisher/qa/local-preparation-targeted/`, fora do versionamento.

O relatório final registra contagens, testes descobertos, falhas/erros, integridade
e isolamento. Nenhum resultado Fake homologa persistência no TikTok real. Mesmo
após regressões direcionadas aprovadas, permanece
`READY_FOR_OPERATIONAL_DEPLOYMENT=false` até gate completo e decisão separada.

## Resultado final da entrega

Foram preparados 30 testes de backend e dois cenários Chrome novos. A seleção
principal aprovou 207 casos (28 novos + 179 regressões existentes), sem falhas,
erros ou skips. A revisão final acrescentou quatro casos e corrigiu a retenção
do evento de cancelamento numa repetição imediata após falha, a recuperação de
claims locais no startup e a conferência do container MP4. Todos os 32 casos
locais foram então executados novamente e aprovados. São **211 casos distintos
aprovados em 239 execuções direcionadas**, sem repetição da suíte completa.

Depois da seleção principal, apenas o coordenador local, seus testes e o hook
de recuperação local no `initialize` foram alterados. Os demais métodos da API,
inclusive GET/POST remotos, permaneceram idênticos por comparação AST. As
instruções anteriores de inicialização também foram preservadas. Os fontes do
renderer, timing, worker remoto, protocolo de prova, histórico e consumo
permaneceram iguais aos exercitados na seleção principal.

Os hashes executáveis da rodada final permaneceram idênticos antes/depois e
foram conferidos novamente ao encerrar. Foram aprovados oito compiles Python,
três checks JavaScript e os checks de diff normal/staged. O índice permanece
sem staging. As quatro worktrees protegidas, main e HEAD foram preservados.
Houve 1022 conexões SQLite permitidas em fixtures; zero conexões bloqueadas,
violações de rede ou erros inesperados nas rodadas finais aprovadas.

Evidências finais:

- `data/publisher/qa/local-preparation-targeted/before-final-targeted-result.json`: 207 aprovados.
- `data/publisher/qa/local-preparation-final/targeted-result.json`: 32 locais aprovados.
- `data/publisher/qa/local-preparation-final/verification-final.json`: união dos casos, integridade, estáticos e isolamento.
- `data/publisher/qa/local-preparation-targeted/local-ready.png` e `local-failure.png`: interface sintética inspecionada.

Logs de desenvolvimento foram mantidos separadamente. As contagens finais se
referem às seleções aprovadas acima. Para reproduzir somente a última rodada:

```powershell
python -B -c 'from tests import publisher_local_preparation_gate as g; g.MODULES=g.MODULES[:2]; g.QA=g.ROOT/"data/publisher/qa/local-preparation-final"; raise SystemExit(g.main())'
```

Inventário de 12 arquivos da entrega, sem artifacts de QA versionados:

- `src/publisher/local_preparation.py`: entidade, pipeline, recuperação, cache local, download e remoção.
- `src/publisher/service.py`: migration aditiva e exclusão mútua de admissão.
- `src/publisher/http.py`: dispatcher local independente e hook de recuperação no startup.
- `src/publisher/media.py`: cancelamento opcional entre chunks, preservando chamadas remotas existentes.
- `dashboard_server.py`: roteamento GET e encerramento do coordenador local.
- `dashboard/publisher_local_preparation.js`: Biblioteca, revisão, progresso e resultados.
- `dashboard/publisher_headlines.js`: reutilização dos controles de seleção existentes.
- `dashboard/publisher.html`: carregamento do módulo local.
- `tests/test_publisher_local_preparation.py`: 30 casos de backend e segurança.
- `tests/test_publisher_local_preparation_browser.py`: dois cenários Chrome isolados.
- `tests/publisher_local_preparation_gate.py`: seleção direcionada e harness de isolamento.
- `docs/publisher-local-preparation.md`: contrato, retenção e relatório.

```text
PUBLISHER_ONLY_SCOPE=true
CURRENT_BRANCH=feature/publisher-p0-integration
CURRENT_HEAD=069fc20c08c398ae846b13c0da470cf38a40dad2
LOCAL_PREPARATION_MODE_IMPLEMENTED=true
LOCAL_PREPARATION_BACKEND_ISOLATED=true
LIBRARY_ENTRY_IMPLEMENTED=true
HEADLINE_CONFIGURATION_SUPPORTED=true
BULK_PREPARATION_SUPPORTED=true
LOCAL_MP4_OUTPUT_VALIDATED=true
LOCAL_DOWNLOAD_AVAILABLE=true
NO_TIKTOK_BROWSER_ACTIONS=true
NO_REMOTE_UPLOAD=true
NO_SAVE_DRAFT=true
NO_REMOTE_PERSISTENCE_PROMOTION=true
NO_DRIVE_DELETION=true
HISTORICAL_MEDIA_GUARD_PRESERVED=true
PUBLICATION_RESERVATIONS_PRESERVED=true
LOCAL_PREPARATION_LIFECYCLE_SAFE=true
FOUR_THREAD_ENCODER_PRESERVED=true
ENCODE_TIMEOUT_PRESERVED=true
CFR_VFR_VALIDATION_PRESERVED=true
CACHE_IDENTITY_PRESERVED=true
FILES_CHANGED=12
TESTS_PREPARED=211
NEW_TESTS_PREPARED=32
TARGETED_TESTS_PASSED=211
TARGETED_TEST_EXECUTIONS_PASSED=239
TARGETED_TESTS_FAILED=0
TARGETED_TESTS_ERRORS=0
STATIC_CHECKS_PASSED=true
OPERATIONAL_DB_WRITES=0
TIKTOK_REMOTE_ACTIONS=0
DRIVE_REMOTE_WRITES=0
ANALYTICS_CODE_CHANGED=false
COMMIT_CREATED=false
MAIN_UNCHANGED=true
READY_FOR_SEPARATE_FULL_GATE=true
READY_FOR_OPERATIONAL_DEPLOYMENT=false
```
