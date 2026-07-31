# Visao geral do projeto TikTok Shop

Data de atualizacao: 2026-07-31

Objetivo: construir uma operacao semi-automatizada para escolher produtos, produzir conteudos em escala, publicar com controle, acompanhar dinheiro real da conta e aprender com as metricas reais das contas TikTok Shop.

## Pilares

Nota operacional de 2026-07-31: a recontagem estrita das 21 pastas de estoque identificou 426 videos validos e 9 produtos abaixo de 8. A regra considera apenas nomes com `GxCyAz` ou `FINAL` e ignora os demais arquivos.

### 1. Inteligencia de mercado

Funcao: descobrir e priorizar produtos.

Fontes:

- EchoTik/Kalodata.
- TikTok Shop analytics.
- Analises locais em Markdown.
- Futuramente API EchoTik via RapidAPI.

Saida esperada:

- produto aprovado, monitorado ou recusado;
- score de oportunidade;
- riscos de compliance;
- angulos criativos iniciais;
- prioridade de producao.

### 2. Producao criativa

Funcao: transformar produto aprovado em assets testaveis.

Ferramentas:

- Google Flow para geracao/edicao inicial;
- prompts padrao validados;
- imagens de ambiente/referencia;
- CapCut para acabamento;
- Gera Videos/Gera Frames para multiplicar variacoes.

Padrao confirmado do Gera:

```text
PRODUTO_GxCyAz_DDMM_ID.mp4
G = gancho
C = corpo
A = action/CTA
```

### 3. Inventario e armazenamento

Funcao: manter controle do que existe, do que esta pronto e do que ja foi usado.

Ferramentas:

- Google Drive como armazenamento;
- pasta `FLOW` como historico atual de producao;
- pasta `TikTok Shop Operacao` como estrutura operacional nova;
- planilha-mae como painel de controle.

Regra: Drive nao gera videos. Drive so armazena e organiza os resultados exportados.

### 4. Operacao de publicacao

Funcao: transformar estoque de videos em rotina de postagem.

Controle:

- produtos em producao;
- lotes Gera completos/parciais;
- videos prontos;
- videos ja postados;
- videos descartados;
- calendario por conta;
- status por publicacao.

Neste inicio, publicacao e limpeza de arquivos ja usados continuam manuais.

### 5. Financeiro e resultados reais

Funcao: acompanhar dinheiro real, saldos, comissoes concluidas e GMV por produto vendido.

Dados:

- GMV total;
- GMV por produto vendido;
- pedidos por produto;
- comissao estimada;
- comissao real/concluida;
- comissao pendente;
- comissao cancelada/estornada;
- saldo disponivel;
- saldo pendente;
- saldo sacado/retirado;
- diferenca entre GMV gerado e comissao efetivamente recebida.

Saida esperada:

- quanto a operacao gerou no total;
- quanto esta realmente concluido;
- quanto ainda esta pendente;
- quais produtos ja venderam;
- quais produtos geraram GMV mas pouca comissao real;
- quais produtos merecem escala por dinheiro recebido, nao so por views;
- visao por conta, por periodo e por produto.

### 6. Metricas e aprendizado

Funcao: cruzar mercado, conteudo e resultado real.

Dados:

- views;
- cliques;
- pedidos;
- GMV;
- comissao estimada;
- comissao real/concluida;
- cancelamentos/devolucoes;
- performance por produto;
- performance por video;
- performance por gancho, corpo e CTA.

Saida esperada:

- quais produtos escalar;
- quais pausar;
- quais criativos reaproveitar;
- quais prompts/imagens base corrigir;
- quais contas e horarios performam melhor.

## Fluxo geral

```text
Mercado / EchoTik / TikTok Shop
> produto candidato
> analise e score
> produto aprovado
> briefing criativo
> Google Flow
> CapCut
> Gera Videos / Gera Frames
> Drive armazena outputs
> inventario dos lotes
> limpeza manual dos ja postados
> revisao visual/audio/compliance
> calendario de publicacao
> publicacao TikTok Shop
> metricas reais de conteudo
> financeiro real da conta
> aprendizado
> proxima rodada de produto/conteudo
```

## Estrutura operacional atual

Google Drive:

- `FLOW`: historico atual com produtos, prompts, imagens e videos gerados.
- `TikTok Shop Operacao`: estrutura nova para organizar produtos, videos, finais, postados e descartados.

Planilha-mae:

- `dashboard`: visao geral.
- `contas`: perfis TikTok Shop.
- `produtos`: produtos aprovados/monitorados.
- `pipeline_ceci`: produtos que estao girando no perfil Ceci, com status de Gera Viral, criativo, venda e financeiro.
- `pipeline_fe`: produtos que estao girando no perfil Fe, com status de Gera Viral, criativo, venda e financeiro.
- `conteudo`: videos individuais.
- `lotes_gera_frames`: rodadas do Gera Videos/Gera Frames.
- `frames_prompts`: imagens base, frame sets e prompts.
- `estoque_videos`: snapshot de videos disponiveis por produto/pasta.
- `calendario_postagens`: matriz diaria de postagem por produto, no ritmo inicial de 2 videos por produto com estoque disponivel por dia.
- `publicacoes`: calendario e posts publicados.
- `metricas_diarias`: resultado real.
- `financeiro_conta`: saldos, comissoes e valores concluidos por conta e periodo.
- `vendas_produto`: GMV, pedidos e comissao por produto vendido.
- `saldos_tiktok`: historico de saldo disponivel, pendente, sacado e ajustes.
- `arquivos_drive`: localizacao dos arquivos.
- `experimentos`: aprendizados por variavel criativa.
- `import_tiktok`: pouso de exports TikTok.
- `import_echotik`: pouso de dados EchoTik.

## Painel financeiro esperado

O dashboard principal deve separar metricas de vaidade, metricas comerciais e dinheiro real.

Indicadores de topo:

- comissao total estimada;
- comissao real/concluida;
- comissao pendente;
- comissao cancelada/estornada;
- saldo disponivel;
- saldo pendente;
- total sacado;
- GMV total;
- pedidos totais;
- ticket medio;
- comissao real por 1000 views;
- GMV por produto vendido.

Tabelas essenciais:

- ranking de produtos por GMV;
- ranking de produtos por comissao real;
- ranking de produtos por pedidos;
- ranking de videos por comissao real;
- conciliacao entre comissao estimada e comissao concluida;
- saldos por conta e por periodo.

## Pipeline por perfil

O pipeline por perfil separa producao de conteudo, venda e financeiro. Cada perfil deve ter uma aba propria quando a rotina operacional ou lista de produtos for diferente.

Pipelines atuais:

- `pipeline_ceci`: produtos girando no perfil `Ceci`.
- `pipeline_fe`: produtos girando no perfil `Fe`.

Regra de leitura:

- `OK GERA`: conteudo ja produzido e multiplicado no Gera Viral/Gera Videos; normalmente existe lote no Drive.
- `NOVO`: produto passou pela validacao inicial de interesse, mas ainda precisa modelar imagens, roteiro e/ou preparar producao.
- Sem `OK GERA`: nao significa que o produto nao vendeu; significa apenas que ainda nao existe producao no Gera Viral desde o inicio desse processo.
- Venda, GMV, comissao e saldo devem vir do import/financeiro TikTok Shop, nao do status de producao.

## Estado atual do estoque FLOW

Snapshot operacional lido da planilha-mae em 2026-07-28:

- 21 produtos/pastas com videos disponiveis.
- 303 videos mapeados no total pela contagem bruta anterior.
- A contagem valida de estoque deve ser recalculada pelo nome do arquivo: somente videos com `G<number>C<number>A<number>` ou `FINAL`.
- Ritmo inicial do calendario: 2 videos por produto com estoque disponivel por dia.
- Horizonte do calendario: 2026-07-22 a 2026-08-04.
- Cobertura maxima atual: 19 dias, puxada por `CEBO DE CARNEIRO / CREME MULHER`.
- Abas de controle: `estoque_videos` e `calendario_postagens`.

Maiores estoques atuais:

- `CEBO DE CARNEIRO / CREME MULHER`: 38 videos, 19 dias.
- `PRO 3 MAGNESIO`: 28 videos, 14 dias.
- `MAQUININHA`: 28 videos, 14 dias.
- `ESCOVA ELETRICA`: 24 videos, 12 dias, perfil pendente.
- `KIT 2 TRAVESSEIROS`: 24 videos, 12 dias.
- `BS BARBOUR'S MASC KIT2`: 16 videos, 8 dias.
- `BS ARABE AYRA + AMBER KIT`: 14 videos, 7 dias, perfil pendente.

Pipelines atuais:

- `pipeline_ceci`: 23 produtos em andamento.
- `pipeline_fe`: 10 produtos em andamento.
- Produtos com status `OK GERA` indicam producao/multiplicacao, nao venda confirmada.

Observacao: alguns videos podem ja ter sido postados e ainda nao excluidos/movidos. Esses lotes devem entrar como `pendente_limpeza_manual` antes de serem tratados como estoque publicavel.

Regra de estoque publicavel: contar apenas arquivos de video cujo nome contenha o padrao `G<number>C<number>A<number>` ou a palavra `FINAL`. Outros `.mp4` devem ser ignorados para fins de alerta de estoque.

## Decisoes de seguranca

## Estoque estrito em 2026-07-30

O inventário diário leu as 21 pastas de estoque e encontrou 193 vídeos válidos. A regra aplicada foi: arquivo de vídeo com `G<number>C<number>A<number>` ou `FINAL` no nome, deduplicado por ID do Drive e com subpastas aplicáveis incluídas. Nenhuma pasta ficou pendente de verificação; não houve alteração no Drive ou na planilha.

## Estoque estrito em 2026-07-29

O inventario diario leu as 21 pastas de estoque e encontrou 188 videos validos. A regra aplicada foi: arquivo de video com `G<number>C<number>A<number>` ou `FINAL` no nome; demais `.mp4` nao entram no estoque. Nao houve pasta inacessivel nem alteracao no Drive ou na planilha.

- Nao apagar arquivos sem autorizacao explicita.
- Nao mover arquivos sem autorizacao explicita.
- Nao publicar sem autorizacao explicita.
- Nao alterar conta, produto vinculado, preco, cupom ou configuracao sem autorizacao explicita.
- Automacao pode inventariar, classificar, preparar calendario e sugerir proximas acoes.

## Proximos passos

1. Validar este desenho de pilares e fluxo.
2. Se aprovado, fazer commit e push.
3. Popular `lotes_gera_frames` com os lotes confirmados.
4. Criar rotina de inventario somente leitura da pasta `FLOW`.
5. Criar estrutura financeira na planilha-mae: `financeiro_conta`, `vendas_produto` e `saldos_tiktok`.
6. Depois da limpeza manual dos ja postados, transformar os lotes limpos em calendario.
7. Cruzar publicacoes com metricas reais da conta.
8. Conciliar GMV, pedidos, comissao estimada, comissao concluida e saldo.
