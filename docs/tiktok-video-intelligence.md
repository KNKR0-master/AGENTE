# Inteligencia de videos TikTok

## Fonte de verdade e conciliacao

O SQLite em `data/agent.db` e a fonte local. As duas familias de dados sao
mantidas separadas e conciliadas somente por TikTok Video ID / Content ID:

- TikTok Studio: catalogo, distribuicao, retencao e engajamento;
- Affiliate Orders XLSX: pedidos, itens, reembolsos, GMV e comissao.

Snapshots de Studio sao imutaveis em `tiktok_video_snapshots`. Reprocessar
scores nao altera o dado bruto. Linhas antigas sem metricas novas continuam
validas.

## Coortes

Cada video e comparado primeiro com videos da mesma faixa de idade:

- `0-2h`;
- `2-6h`;
- `6-12h`;
- `12-24h`;
- `24-48h`;
- `2-3d`;
- `4-7d`;
- `8-14d`;
- `15-30d`;
- `30d+`.

Quando existe amostra, o percentil combina referencia da conta (60%) e do
produto (40%). Dados insuficientes produzem `NULL`, `INSUFFICIENT_DATA` ou baixa
confianca, nunca uma certeza artificial.

## Investment Score

O score e deterministico, explicavel e normalizado somente pelos componentes
disponiveis:

| Componente | Peso |
| --- | ---: |
| Eficiencia comercial | 25 |
| Velocidade de GMV | 20 |
| Velocidade de pedidos | 15 |
| CTR/CVR de produto | 15 |
| Retencao | 10 |
| Distribuicao inicial | 10 |
| Engajamento | 5 |

Como CTR/CVR nao existe na fonte atual, esse peso nao e preenchido. A confianca
considera tamanho da amostra, pedidos, cliques, idade do video e cobertura das
metricas. O dashboard sempre mostra score, confianca e motivos.

Recomendacoes:

- `ESCALAR`: score >= 80 e confianca >= 65;
- `TESTAR_COM_BAIXO_ORCAMENTO`: score >= 62;
- `NAO_ESCALAR`: score < 40 com confianca >= 50;
- `EM_DECAY`: momentum classificado como `DECAYING` ou `DEAD`;
- `OBSERVAR`: amostra incompleta, baixa confianca ou zona intermediaria.

Essas recomendacoes nao criam campanha, nao alteram orcamento e nao publicam
conteudo automaticamente.

## Distribution Health

Compara videos publicados nos ultimos 7 dias contra os 7 dias anteriores:

- mediana de views em 24h e 48h;
- GMV por mil views;
- pedidos por mil views;
- novos vencedores e vencedores perdidos.

Diagnosticos:

- `DISTRIBUTION_DROP`: views 24h abaixo de 75% com eficiencia comercial estavel;
- `CONVERSION_DROP`: distribuicao preservada e eficiencia abaixo de 75%;
- `PRODUCT_DROP`: deterioracao concentrada em parte dos produtos com baseline;
- `ACCOUNT_WIDE_DROP`: ambas abaixo de 75%;
- `HEALTHY`;
- `INSUFFICIENT_DATA` quando faltam ao menos 3 videos em cada periodo ou o
  comparativo nao pode ser calculado.

## Alertas

Os alertas sao idempotentes por video/dia ou conta/dia:

- candidato a investimento;
- vencedor em decay;
- queda de distribuicao;
- lacuna de reposicao de vencedores.

Eles aparecem na aba `Inteligencia de videos`. O sistema nao envia comandos ao
TikTok e nao decide investimento sem revisao humana.

## Tags e testes criativos

`tiktok_creative_tags` guarda correcoes manuais de familia criativa, hook,
formato, CTA, dor, promessa, visual, produto e variante. A tabela de
experimentos relaciona hipotese, variavel testada, controle, variante e janela
de avaliacao sem duplicar o video nem seu snapshot.

## Validacao real inicial

Em 2026-08-16 foram persistidos cinco videos reais da conta
`cecilia.figueired65`:

- quatro publicacoes recentes, entre 38 e 223 views;
- um video de 2026-07-07 com 1,1 milhao de views e 969 pedidos conciliados;
- todos ficaram em `OBSERVAR`, pois a amostra recente e pequena e o Affiliate
  Orders disponivel nao cobre as publicacoes de 2026-08-15;
- Distribution Health ficou `INSUFFICIENT_DATA`, pois ainda nao existe coorte
  comparavel na semana anterior.

Esse resultado e intencional: o sistema prefere declarar falta de evidencia a
transformar dados antigos ou ausentes em recomendacao forte.
