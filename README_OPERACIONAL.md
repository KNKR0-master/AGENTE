# Fluxo operacional do agente TikTok Shop

## Objetivo

Transformar dados crus do Kalodata, EchoTik e da planilha-mae do Drive em uma base local de decisao:

- produto monitorado;
- snapshots de mercado;
- top videos;
- top criadores;
- status de validacao;
- fila de producao com no minimo duas versoes por produto validado;
- espelho operacional do Drive/Sheets para acompanhamento local.

## Fontes atuais

- Drive: `TikTok Shop Operacao`.
- Planilha: `Operacao TikTok Shop - Planilha Mae`.
- Snapshot local mais recente: `DRIVE_SNAPSHOT_2026-07-28.md`.
- Configuracao observada: `dados/drive_operacao_tiktok_shop.json`.

## Como aproveitamos a branch `main`

A branch `main` adicionou a camada operacional que complementa este MVP local:

- `VISAO_GERAL_PROJETO.md`: visao integrada dos pilares do projeto.
- `OPERACAO_TIKTOK_SHOP.md`: mapa da planilha-mae, contas, produtos, conteudo, calendario e metricas.
- `FINANCEIRO_RESULTADOS_TIKTOK.md`: conciliacao de GMV, comissao estimada, comissao concluida, saldos e saques.
- `GOOGLE_FLOW.md` e `VALIDACAO_GOOGLE_FLOW.md`: regras para transformar produto validado em criativos.
- `conteudo/google-flow/`: inventario, multiplicacao de frames e revisao economica de videos.
- `dados/drive_operacao_tiktok_shop.json`: IDs, URLs, abas e estado observado do Drive/Sheets.

Este dashboard fica como a camada local de leitura, decisao e acompanhamento. A planilha/Drive ficam como camada operacional externa ate autorizarmos automacoes de escrita.

## Banco local

O banco SQLite fica em `data/agent.db`.

Ele guarda:

- `products`: cadastro e decisao do produto;
- `product_snapshots`: totais por janela de analise;
- `video_metrics`: linhas da exportacao de videos;
- `creator_metrics`: linhas da exportacao de criadores;
- `production_tasks`: versoes de video para produzir, postar, programar e medir.
- `content_items`: videos proprios importados, validados e vinculados ao produto;
- `publishing_queue`: fila manual de postagem para o iPhone;
- `content_performance`: performance manual ou importada por video publicado;
- `external_sync_events`: historico idempotente de sincronizacao com Drive/Sheets.

O arquivo `.db` e local e nao entra no Git.

## Importar exportacoes do Kalodata

Exemplo:

```powershell
python ingest_kalodata.py `
  --video-xlsx "C:\Users\Usuario\Downloads\Kalodata_Video_20260714071513_BR.xlsx" `
  --creator-xlsx "C:\Users\Usuario\Downloads\Kalodata_Creator_20260714071447_BR.xlsx" `
  --price 64 `
  --commission-rate 15 `
  --shop-name "Manzanas estofadas" `
  --validated
```

Use `--validated` quando o produto ja deve entrar na fila de producao. Isso cria automaticamente as versoes 1 e 2.

## Abrir dashboard local

```powershell
python dashboard_server.py
```

Depois abra:

```text
http://127.0.0.1:8765
```

A aba `Inteligencia de videos` reune distribuicao, retencao, engajamento,
conciliacao comercial por Content ID, Investment Score, decay, reposicao de
vencedores e alertas explicaveis.

## Analytics diario do TikTok Studio

O coletor usa readiness deterministico em modo `SAFE`: espera os campos
obrigatorios, tres leituras iguais, DOM quieto e ausencia de requests relevantes
antes de extrair ou navegar para o proximo video. O dashboard exibe completos,
parciais, timeouts, tentativas, tempo medio/P95 e completude da ultima execucao.

O catalogo e as metricas por video podem ser coletados com uma sessao TikTok
autenticada manualmente:

```powershell
pip install -r requirements.txt
python -m playwright install chromium
python collect_tiktok_analytics.py --setup-session
python collect_tiktok_analytics.py --last-n-days 30
python collect_tiktok_analytics.py --remap --last-n-days 30
```

O agendamento diario das 07:00 chama `run_tiktok_analytics_daily.ps1`. A coleta
nao guarda senha e para em `AUTH_REQUIRED` ou `INTERACTION_REQUIRED` quando login, CAPTCHA ou MFA exigem
intervencao humana.

Documentacao:

```text
docs/tiktok-studio-browser-analytics.md
docs/tiktok-video-intelligence.md
docs/tiktok_analytics.md
```

## Pipeline de conteudo TikTok Shop

A aba `Conteudo` do dashboard controla o estoque proprio de videos, a fila de publicacao e o proximo item a enviar manualmente para o iPhone.

Documentacao completa:

```text
docs/tiktok-content-pipeline.md
docs/tiktok-shop-creator-api.md
docs/tiktok-affiliate-orders-daily-import.md
docs/drive-content-inventory.md
```

Atalho CLI:

```powershell
python manage_content.py next
```

## TikTok Shop Creator API

A extensao oficial de perfil, pedidos afiliados, GMV e comissoes esta preservada sob feature flag. Para BR ela permanece desativada, pois Creator Collaboration ainda nao esta aberto para esse caminho.

Sincronizacao avulsa, depois de conectar a conta Creator:

```powershell
python sync_affiliate.py
```

A operacao atual usa `TikTokReportProvider` para o XLSX oficial Affiliate Orders e `ManualAffiliateProvider` como fallback. O upload diario possui preview antes do processamento. Coloque exportacoes do TikTok Shop em `data/affiliate_reports/inbox`; com o dashboard aberto, arquivos novos e estaveis sao detectados automaticamente. Tambem e possivel executar:

```powershell
python import_affiliate_reports.py
```

O SQLite continua sendo a fonte de verdade. Arquivos repetidos sao identificados por SHA-256, pedidos recebem UPSERT e a atribuicao a video so ocorre por match direto entre Content ID TikTok e `tiktok_video_id`.

## Automacao Kalodata via navegador

Proxima camada planejada:

1. Receber nome ou link do produto.
2. Abrir Kalodata no navegador autenticado via Di-cloak.
3. Buscar o produto.
4. Selecionar janela de analise.
5. Exportar ou extrair dados de produto, videos e criadores.
6. Importar no banco local.
7. Atualizar o dashboard.

Por envolver sessao logada, cookies e navegacao em conta real, essa automacao so deve ser ativada com aprovacao explicita antes do primeiro uso.

## Regra de producao

Quando um produto for marcado como `validated`, o sistema cria duas tarefas:

- `Versao 1`: postar na hora;
- `Versao 2`: deixar programada.

Cada versao pode registrar:

- status;
- gancho;
- formato;
- data postada;
- data programada;
- observacoes;
- metricas posteriores.

## Regra de contagem de estoque

Para alerta diario e planejamento de postagem, estoque valido nao e qualquer `.mp4` na pasta.

Contam apenas videos cujo nome tenha:

- padrao Gera Viral/Gera Frames `G<number>C<number>A<number>`, por exemplo `G1C2A3`; ou
- palavra `FINAL`, sem diferenciar maiusculas/minusculas.

Todo outro `.mp4` deve ser tratado como arquivo auxiliar, antigo, rascunho ou pendente de verificacao, ate revisao manual.

A contagem atual usa `VideoPublicationEligibilityService`: alem do nome, o
arquivo precisa existir no Drive, estar em pasta mapeada, ter validacao tecnica
`PASS` e Content `READY`. Publicados, itens na fila, duplicatas, invalidos e
arquivos ausentes do Drive nao entram em "disponiveis para publicacao".
