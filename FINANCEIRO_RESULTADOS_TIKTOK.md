# Financeiro e resultados reais TikTok Shop

Data de criacao: 2026-07-21

Objetivo: controlar o dinheiro real da operacao, separando metricas de conteudo, GMV gerado, comissao estimada, comissao concluida, saldos e saques.

## Perguntas que esta camada responde

- Quanto a operacao gerou de GMV?
- Quanto virou comissao estimada?
- Quanto virou comissao real/concluida?
- Quanto esta pendente?
- Quanto foi cancelado, estornado ou perdido?
- Quanto esta disponivel para saque?
- Quanto ja foi sacado?
- Quais produtos ja venderam?
- Qual produto gerou mais dinheiro real, nao apenas mais views?

## Conceitos

### GMV

Valor bruto vendido associado aos pedidos.

Uso: medir volume comercial por produto, conta, video e periodo.

### Comissao estimada

Comissao prevista enquanto o pedido ainda pode estar pendente, em processamento, sujeito a cancelamento ou devolucao.

Uso: medir potencial, mas nao tratar como dinheiro recebido.

### Comissao concluida / real

Comissao confirmada apos o pedido passar pelas etapas necessarias da plataforma.

Uso: medir resultado real da operacao.

### Comissao pendente

Comissao ainda nao concluida.

Uso: prever receita futura, mas com risco.

### Saldo disponivel

Valor que pode ser sacado.

Uso: caixa real disponivel.

### Saldo pendente

Valor ainda bloqueado/em maturacao/processamento.

Uso: pipeline financeiro.

### Saques

Valores retirados da plataforma.

Uso: conciliacao entre saldo, dinheiro recebido e historico financeiro.

## Abas recomendadas na planilha-mae

### `financeiro_conta`

Uma linha por conta e periodo.

Campos:

- periodo_inicio;
- periodo_fim;
- conta_id;
- GMV_total;
- pedidos_total;
- comissao_estimada;
- comissao_pendente;
- comissao_concluida;
- comissao_cancelada;
- comissao_estornada;
- saldo_disponivel;
- saldo_pendente;
- saldo_sacado;
- ajustes;
- fonte_export;
- observacoes.

### `vendas_produto`

Uma linha por produto vendido, conta e periodo.

Campos:

- data;
- conta_id;
- produto_id;
- produto_nome;
- pedidos;
- unidades;
- GMV;
- comissao_estimada;
- comissao_concluida;
- comissao_pendente;
- cancelamentos;
- devolucoes;
- ticket_medio;
- comissao_por_pedido;
- fonte_export.

### `saldos_tiktok`

Uma linha por snapshot de saldo ou evento de saque.

Campos:

- data_snapshot;
- conta_id;
- saldo_disponivel;
- saldo_pendente;
- saldo_em_processamento;
- total_sacado;
- saque_id;
- data_saque;
- valor_saque;
- status_saque;
- observacoes.

## Dashboard financeiro

Cards principais:

- GMV total.
- Pedidos totais.
- Comissao estimada.
- Comissao concluida.
- Comissao pendente.
- Saldo disponivel.
- Saldo pendente.
- Total sacado.
- Comissao concluida / GMV.
- Comissao concluida por 1000 views.

Graficos:

- GMV por dia.
- Comissao concluida por dia.
- Saldo disponivel vs saldo pendente.
- Ranking de produtos por GMV.
- Ranking de produtos por comissao concluida.
- Ranking de videos por comissao concluida.
- Diferenca entre comissao estimada e concluida.

## Regra de decisao

Para escalar produto, priorizar nesta ordem:

1. Comissao concluida.
2. GMV por produto vendido.
3. Pedidos reais.
4. Comissao pendente com tendencia de conclusao.
5. Views e cliques apenas como sinais auxiliares.

Um produto com muitas views, mas sem GMV/comissao concluida, deve ser tratado como aprendizado criativo, nao como produto vencedor.

Um produto com GMV baixo, mas com comissao concluida consistente e bom custo operacional, pode ser candidato a escala controlada.

## Conciliacao minima

Para cada periodo:

```text
comissao_estimada
- comissao_cancelada
- comissao_estornada
- comissao_ainda_pendente
= comissao_concluida_aproximada
```

E:

```text
saldo_anterior
+ comissao_concluida
- saques
+ ajustes
= saldo_atual_aproximado
```

Essas formulas sao checks de sanidade. A fonte oficial/export da plataforma continua sendo a referencia final.

## Proxima automacao

1. Receber primeiro export/print estruturado do TikTok Shop com saldos e comissoes.
2. Mapear nomes reais das colunas da plataforma.
3. Popular `import_tiktok`.
4. Normalizar para `financeiro_conta`, `vendas_produto` e `saldos_tiktok`.
5. Atualizar dashboard financeiro.
