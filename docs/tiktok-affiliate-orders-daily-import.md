# TikTok Affiliate Orders Daily Import

## Relatorio real analisado

O formato autoritativo e o XLSX `affiliate_orders_7671532453370840839.xlsx`, sheet `Sheet1`, com 47 colunas e 6.305 linhas de dados. A amostra contem:

- 6.293 pedidos unicos;
- 12 pedidos com duas linhas porque possuem SKUs distintos;
- 63 IDs de produto e 182 IDs de SKU;
- 339 IDs de conteudo;
- 6.299 linhas de Video, 4 de Vitrine e 2 de Live;
- pedidos `Pedido de anuncios da loja` e `Pedido de Afiliados`;
- status `Liquidado`, `Inelegivel`, `Pendente` e `AwaitingPayment`;
- periodo de 25/06/2026 23:57:28 a 08/08/2026 20:37:02, horario da conta BR.

A chave do pedido e `(provider, external_order_id)`. A chave de item e `(affiliate_order_id, external_sku_id)`. Os 12 IDs repetidos no arquivo sao pedidos com mais de um SKU, nao duplicidades de venda.

Todos os IDs externos TikTok sao persistidos e enviados ao frontend como `TEXT`/string. Isso inclui pedido, produto, SKU, conteudo e codigo da loja.

## Campos reconhecidos

O detector usa nomes de cabecalho, nunca posicoes. Ele normaliza BOM, acentos, whitespace, quebras de linha e espacos duplicados. Os 47 campos atuais cobrem:

- pedido, SKU, produto, preco, itens vendidos e itens reembolsados;
- loja, codigo da loja, agencias, moeda, tipo/status do pedido e comissao;
- tipo e ID do conteudo;
- taxas de padrao, anuncios, bonus e participacao;
- GMV;
- bases, componentes, bonus, participacoes e impostos estimados;
- bases, componentes, bonus, compartilhamento e impostos reais;
- valor total final recebido;
- data do pedido e data de liquidacao.

Campos criticos: ID do pedido, ID do produto, tipo de conteudo, ID do conteudo, GMV e data do pedido. Uma linha invalida e registrada em `affiliate_import_errors` sem interromper as linhas validas. Colunas novas produzem `COMPATIBLE_WITH_NEW_COLUMNS`; remocao de campo critico produz `MISSING_REQUIRED_COLUMNS`.

## Precisao financeira

O parser usa `Decimal`. O banco grava valores monetarios do relatorio como centavos inteiros em colunas `*_minor`; `63,36` vira `6336`. Aceita `R$ 1.234,56`, `1.234,56`, `1234,56` e `1234.56`. A moeda atual aceita e `BRL`.

Os campos originais sao preservados separadamente. As metricas derivadas seguem estas regras:

- `estimated_commission_minor`: soma comissao padrao estimada, comissao estimada de anuncios, bonus estimado, bonus da agencia parceira e participacao na receita estimada;
- `actual_commission_minor`: soma comissao padrao, comissao de anuncios, bonus e bonus da agencia parceira;
- bases de comissao e impostos nao entram nessas somas;
- `Compartilhado com parceiro` permanece separado e nao e tratado como ganho;
- `final_received_minor` vem somente de `Valor total final recebido` e nunca e confundido com GMV ou comissao;
- reembolso e contagem de itens; o sistema nao inventa valor financeiro de reembolso.

No XLSX analisado, o GMV total e R$ 262.488,35, com 6.424 itens vendidos e 23 reembolsados. A comissao estimada derivada e R$ 28.913,73; a comissao real derivada e R$ 13.939,46; o valor final recebido informado tambem soma R$ 13.939,46. Esses totais permanecem auditaveis por componente e por raw row.

## Importacao

Na aba `Atualizacao TikTok`:

1. selecione o XLSX exportado em TikTok Shop Creator > Affiliate Orders;
2. use `Atualizacao diaria (30 dias)` para a rotina ou `Historico completo` na primeira carga;
3. clique `Analisar arquivo`;
4. confira periodo, pedidos, produtos, SKUs, Content IDs e matching;
5. clique `Processar atualizacao`.

O preview nao altera pedidos. O processamento faz UPSERT, atualiza mudancas de liquidacao/reembolso e registra o batch. O SHA-256 detecta arquivo repetido; `Reprocessar arquivo` e permitido para aplicar novos mappings sem duplicar vendas.

A pasta `data/affiliate_reports/inbox` continua sendo monitorada. Arquivos estaveis sao importados como `DAILY_UPDATE`, com `DAILY_IMPORT_LOOKBACK_DAYS=30`. O arquivo de origem nao e movido, renomeado ou apagado.

## Matching

Produtos sao buscados por TikTok Product ID, TikTok SKU ID, `ProductExternalReference` e associacao manual. Nome normalizado so e fallback quando existe um unico produto local correspondente. Uma associacao manual e preservada em reprocessamentos.

Para conteudo `VIDEO`, o sistema compara `external_content_id` com `content_items.tiktok_video_id`. Match direto recebe `OFFICIAL / DIRECT_CONTENT_ID / 1.0`. `SHOWCASE` e `LIVE` nunca sao vinculados automaticamente a video. Video sem match entra em `TikTok Content Reconciliation` como `UNMATCHED`; pode ser associado, ignorado ou marcado `LEGACY`.

Ao registrar TikTok URL/Video ID em `Marcar publicado`, pedidos historicos daquele ID sao reconciliados imediatamente, sem nova importacao. Nao existe heuristica temporal automatica.

## Dashboard e analytics

A tela diaria mostra GMV, pedidos, itens, reembolsos, comissao estimada, comissao real e valor final recebido; comparacoes hoje/ontem, 7d/7d e 30d/30d; tipos Video/Vitrine/Live; rankings de produto, conteudo e loja; pipeline READY/QUEUED; e `ContentProductionOpportunity` deterministico e explicavel.

Por Content associado, as consultas calculam GMV, pedidos e comissao acumulados em D0, D1, D3, D7, D14 e D30 desde `published_at`. Por produto, estao disponiveis janelas de 24h, 7d e 30d.

`TikTokVideoPerformanceImporter` permanece apenas como interface. CTR, conversao por view, GMV por mil views e pedidos por mil views ficam em `WAITING_FOR_VIDEO_ANALYTICS` ate existir um relatorio oficial real. Nao ha scraper nem automacao da interface TikTok.

## Providers

```text
AffiliateDataProvider
|- TikTokReportProvider          ATIVO: Affiliate Orders XLSX/CSV
|- TikTokShopCreatorProvider     FUTURO: feature flag false no BR
`- ManualAffiliateProvider       FALLBACK
```

`TikTokShopCreatorProvider` nao foi removido e deve produzir os mesmos modelos internos quando a API oficial abrir para BR.
