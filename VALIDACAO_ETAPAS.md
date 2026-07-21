# Validacao por etapas - Inteligencia TikTok Shop

Fonte base: `Projeto_Inteligencia_Produtos_Performance_TikTokShop_v2.docx`

Objetivo deste arquivo: transformar o projeto em checkpoints pequenos, testaveis e auditaveis. A regra do inicio e validar uma etapa por vez, com dados reais ou amostras controladas, antes de automatizar coleta, calculos ou alertas.

## Norte do projeto

O sistema nao deve virar um relatorio preso a Kalodata, Echo Data ou qualquer fornecedor especifico.

O produto final sera um sistema proprio de decisao com tres camadas:

1. `Market Opportunity Score`: oportunidade externa do produto.
2. `Personal Fit Score`: aderencia ao historico real da conta.
3. `Production Priority Score`: prioridade final de producao.

No inicio, quando ainda houver pouco historico proprio, usar regra de cold start:

```text
Production Priority Score inicial =
85% Market Opportunity Score
+ 15% Execution Readiness Score
```

## Etapa 0 - Dicionario e auditoria de fontes

Status: primeira etapa a validar.

Pergunta de validacao:

```text
Temos dados suficientes, estaveis e permitidos para calcular um ranking minimo sem inventar precisao?
```

Entradas esperadas:

- Exportacao CSV/XLSX ou captura estruturada de Echo Data, Kalodata ou fonte equivalente.
- Exportacao ou registro manual dos resultados reais da conta.
- Lista de campos disponiveis por fonte.
- Planilha operacional `Operacao TikTok Shop - Planilha Mae`, quando a rotina de conta estiver em uso.

Checklist:

- Mapear campos de produto: id, nome, categoria, loja, mercado, preco, comissao.
- Mapear demanda: GMV/receita, unidades, janelas 1d, 7d, 30d.
- Mapear criadores: ativos, novos, historicos, top criadores, videos/lives recentes.
- Mapear conteudo: video_id, criador, data, views, produto relacionado.
- Mapear estoque: exato, informado, proxy ou desconhecido.
- Mapear propria conta: views, cliques, pedidos, GMV, comissao estimada, comissao concluida, saldos, saques, cancelamentos.
- Classificar cada campo como `exato`, `estimado`, `proxy`, `manual` ou `ausente`.
- Definir IDs canonicos para produto, loja, criador e video.

Saida validada:

- Matriz de fonte por campo.
- Lista de campos que entram no MVP.
- Lista de campos adiados.
- Nivel de confianca inicial por fonte.

Nao automatizar ainda:

- Scraping pesado.
- Login em plataformas.
- Coleta recorrente.
- Alertas.

## Etapa 1 - MVP manual e seletivo

Pergunta de validacao:

```text
Com poucos produtos escolhidos manualmente, os calculos geram uma decisao util?
```

Escopo recomendado:

- 10 a 30 produtos no primeiro teste.
- 1 categoria ou grupo de categorias proximas.
- 7 a 14 dias de snapshots, se possivel.
- Resultados reais da conta mesmo que ainda sejam poucos.
- Resultados financeiros reais: comissao concluida, saldo disponivel, saldo pendente e GMV por produto vendido.

Calculos minimos:

- Crescimento de receita 7d.
- Crescimento de unidades 7d.
- Velocidade 3d e 7d.
- Aceleracao curta.
- Criadores ativos 7d.
- Crescimento de criadores 7d.
- Receita por criador.
- Demand Supply Gap.
- Comissao potencial 7d.
- Stock Confidence Level.
- Market Opportunity Score inicial.
- Execution Readiness Score manual.
- Production Priority Score inicial.
- Comissao real por produto.
- GMV real por produto vendido.
- Diferenca entre comissao estimada e comissao concluida.

Relatorio visual minimo:

- Tabela executiva com uma linha por produto.
- Grafico de quadrantes:
  - eixo vertical: forca da tendencia;
  - eixo horizontal: saturacao de criadores;
  - tamanho da bolha: GMV/receita;
  - cor: estoque ou confianca de estoque.
- Card de produto com decisao: produzir, testar, monitorar ou descartar.

Saida validada:

- Ranking faz sentido para revisao humana.
- Os motivos do score sao explicaveis.
- O relatorio mostra claramente lacunas e confianca.
- Nenhum campo estimado aparece como se fosse exato.

## Etapa 2 - Dashboard e ciclo de decisao

Pergunta de validacao:

```text
Conseguimos transformar ranking em rotina de producao e feedback?
```

Fluxo minimo:

1. Produto entra no ranking.
2. Produto recebe decisao operacional.
3. Se aprovado, vira plano de teste de conteudo.
4. Cada video recebe hipotese principal.
5. Resultados sao medidos em 24h, 72h, 7d, 14d e 30d.
6. O resultado volta para o historico da conta.

Quando Google Flow for usado na producao:

- Registrar produto_id, prompt, modelo, assets/ingredientes, creditos estimados, arquivo exportado e restricoes de compliance.
- Tratar cada variacao de video como experimento ligado a uma hipotese: gancho, avatar, demonstracao, prova, oferta ou CTA.
- Em categorias sensiveis, como suplementos, gerar somente roteiros e cenas sem promessa medica ou alegacao nao comprovada.

Relatorios visuais:

- Ranking de mercado.
- Ranking de producao.
- Tela de produto individual.
- Tela de desempenho por video.
- Tela financeira por conta.
- Tela de GMV e comissao por produto vendido.
- Tela de saldos, pendencias e saques.
- Tela de alertas.
- Historico de decisoes e resultados.

Automacoes permitidas nesta etapa:

- Importacao assistida de CSV/XLSX.
- Geracao diaria de relatorio a partir de dados ja coletados.
- Alertas locais ou em planilha quando criterios simples forem atendidos.

Automacoes ainda sensiveis:

- Login automatico em plataformas.
- Envio automatico de mensagens.
- Publicacao ou alteracao de contas.
- Coleta em escala sem limite de custo ou termos de uso claros.

## Etapa 3 - Automacao da fonte escolhida

Pergunta de validacao:

```text
A fonte escolhida e estavel o bastante para coleta recorrente?
```

Pre-condicoes:

- Etapa 0 concluida.
- Etapa 1 gerando decisoes uteis.
- Campos essenciais conhecidos.
- Limites de uso documentados.
- Risco de custo e termos de uso entendido.

Automacoes candidatas:

- Importar exportacoes padronizadas.
- Baixar relatorios autorizados.
- Consultar API oficial ou comercial, quando houver.
- Registrar logs de coleta.
- Registrar falhas, lacunas e atrasos.
- Normalizar payloads para tabelas canonicas.

Regras obrigatorias:

- Toda coleta deve registrar `source`, `collected_at`, `source_updated_at`, `data_quality`, `is_estimated` e `raw_payload_reference`.
- Nenhum dado antigo deve sobrescrever snapshot historico.
- Falhas de coleta devem ser visiveis no relatorio.
- Coleta com custo por credito deve ter limite diario.

## Etapa 4 - Aprendizado e recalibracao

Pergunta de validacao:

```text
O score esta prevendo resultado real para a nossa conta?
```

Indicadores de aprendizado:

- Produtos recomendados vs produtos produzidos.
- Videos por produto.
- Views, cliques, pedidos, GMV e comissao.
- Comissao estimada vs comissao concluida.
- Saldo disponivel, saldo pendente e total sacado.
- GMV por produto efetivamente vendido.
- Cancelamentos e devolucoes.
- Comissao por 1000 views.
- Hit rate por categoria, formato, gancho e avatar.
- Persistencia de vendas apos 72h.

Recalibracoes:

- Ajustar pesos do Market Opportunity Score.
- Ajustar peso do Personal Fit Score por volume de historico.
- Penalizar sinais que geram falso positivo.
- Aumentar peso de categorias e formatos em que a conta performa melhor.

## Primeiro sprint recomendado

Duracao sugerida: 3 a 5 dias de trabalho leve.

Meta:

```text
Criar o primeiro relatorio visual manual com 10 a 30 produtos e decidir quais 3 produtos seriam testados.
```

Tarefas:

1. Separar uma exportacao ou captura da fonte candidata com produtos.
2. Separar uma exportacao ou registro da propria conta com videos e pedidos.
3. Registrar os videos atuais na planilha operacional, mesmo que inicialmente de forma manual.
4. Criar dicionario de campos disponiveis.
5. Montar uma planilha ou tabela local de snapshots.
6. Calcular 5 a 8 indicadores essenciais.
7. Gerar primeira tabela executiva.
8. Gerar grafico de quadrantes.
9. Revisar manualmente os top produtos.
10. Registrar decisao e justificativa.
11. Definir o que faltou para automatizar.

## Dados que precisamos pedir/obter primeiro

- Exportacao CSV/XLSX de produtos da fonte escolhida, mesmo que pequena.
- Exportacao CSV/XLSX dos resultados da conta, se disponivel.
- Lista ou pasta dos videos atuais no Google Drive.
- Print ou lista das colunas disponiveis em Echo Data/Kalodata.
- Exemplos de 3 produtos que voce considera bons.
- Exemplos de 3 produtos que parecem bons, mas voce nao produziria.
- Lista de categorias prioritarias.

## Criterio para avancar

So avancar para automacao quando:

- O relatorio manual gerar uma decisao clara.
- As formulas forem explicaveis.
- Os dados tiverem origem rastreavel.
- O usuario concordar que os top produtos parecem razoaveis.
- As lacunas estiverem marcadas em vez de escondidas.
