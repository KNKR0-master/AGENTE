# TikTok Analytics

## Arquitetura

O modulo combina duas fontes sem misturar proveniencia:

1. `TikTokStudioBrowserCollector` descobre videos e coleta distribuicao,
   engajamento, retencao e audiencia em uma sessao autenticada manualmente.
2. `TikTokReportProvider` importa o Affiliate Orders XLSX e fornece pedidos,
   itens, GMV, comissao e origem Shop Ads.
3. `external_content_id` / TikTok Video ID e a unica chave de conciliacao entre
   as fontes.
4. `tiktok_video_snapshots` preserva cada leitura; `tiktok_video_scores` guarda
   os derivados recalculaveis.
5. O dashboard consome `/api/tiktok-analytics` e nao executa investimento,
   publicacao ou mudanca de campanha.

Detalhes do navegador e seletores: `docs/tiktok-studio-browser-analytics.md`.
Regras analiticas: `docs/tiktok-video-intelligence.md`.

## Autenticacao e execucao

```powershell
pip install -r requirements.txt
python -m playwright install chromium
python collect_tiktok_analytics.py --setup-session
python collect_tiktok_analytics.py
```

A senha nao e armazenada pelo projeto. Login, CAPTCHA e MFA sao sempre
concluidos pelo usuario. A sessao persistente local fica em
`data/tiktok-browser-profile` e nao entra no Git.

Comandos adicionais:

```powershell
python collect_tiktok_analytics.py --force
python collect_tiktok_analytics.py --recompute-only
python collect_tiktok_analytics.py --scheduled
```

## Execucao agendada

A tarefa `TikTok Analytics Daily` executa
`run_tiktok_analytics_daily.ps1` diariamente as 07:00, usando o fuso
`America/Sao_Paulo`. Os logs ficam em `data/logs`.

Uma coleta concluida nao e repetida no mesmo dia sem `--force`. Uma coleta
parcial nunca recebe status de sucesso completo.

## Campos coletados

- identificacao: Video ID, URL, data, legenda, duracao, thumbnail e status;
- distribuicao: views, alcance, origens Para voce, Seguindo, Pesquisa, Perfil,
  Outro e Som;
- audiencia: taxa de espectadores novos e recorrentes;
- engajamento: likes, comentarios, compartilhamentos, salvos e seguidores;
- retencao: tempo total, tempo medio e assistiu completo.

Cada snapshot guarda `source`, `captured_at`, `extraction_run_id`, `page_url`,
valores brutos e proveniencia por metrica. Ausencia e gravada como `NULL`.

## Campos indisponiveis

Nas telas web validadas nao estavam disponiveis por video: impressoes de
produto, cliques, CTR, add-to-cart, GMV, pedidos, comissao, conversion rate e
Shop Ads. Os campos comerciais sao preenchidos apenas pelo Affiliate Orders.

O acesso TikTok Shop para Criadores dentro do Studio web abre um QR para o app,
sem uma tela web adicional de analytics. Nenhum formato ou parser hipotetico
foi criado.

## Scoring e alertas

O Investment Score usa percentis por idade e produto e pondera eficiencia
comercial, velocidades de GMV/pedidos, CTR/CVR, retencao, distribuicao inicial
e engajamento. Componentes ausentes sao removidos do denominador. Confidence
Score usa views, idade, cliques, pedidos e cobertura.

Alertas suportados: candidato a investimento, vencedor em decay, queda de
distribuicao e lacuna de reposicao. Todos sao explicaveis e idempotentes.

## Troubleshooting

- `AUTH_REQUIRED`: execute novamente `--setup-session` e conclua o login.
- `UI_CHANGED`: confira o screenshot em `data/tiktok-analytics-artifacts` e
  atualize `config/tiktok_studio_mapping.json` com seletores observados.
- `RATE_LIMITED`: aguarde e rode novamente; o coletor usa retry limitado e
  backoff, sem contornar protecoes.
- metricas `NULL`: confirme se a tela realmente exibe o campo e se o Affiliate
  Orders cobre a data do video.
- score com baixa confianca: acumule snapshots e importe um relatorio comercial
  recente; nao substitua ausencia por zero.
