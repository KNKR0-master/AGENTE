# Operacao TikTok Shop

Data de criacao: 2026-07-21

Objetivo: organizar a rotina das contas TikTok Shop, os produtos em producao, o estoque de videos, a programacao de postagens e os resultados reais da conta.

## Estrutura criada

Pasta Google Drive:

- `TikTok Shop Operacao`
- URL: `https://drive.google.com/drive/folders/1EBc8qmZcDHoF7et-2ZzK3qY8CMWnKDsU`

Planilha-mae:

- `Operacao TikTok Shop - Planilha Mae`
- URL: `https://docs.google.com/spreadsheets/d/12wEBjSpfBBqm0Zj4sncafhjnGnt6SH_YWZUtbQDIq48/edit`

## Papel desta camada

Esta camada transforma o projeto em rotina operacional:

```text
Produtos validados
> videos gerados/editados
> Gera Videos / Gera Frames
> lotes de videos por produto
> Drive como armazenamento dos outputs
> calendario de publicacao
> posts publicados
> metricas reais da conta
> financeiro real: GMV, comissao concluida, saldos e saques
> aprendizado por produto, conta, ambiente, gancho e formato
```

Ela complementa a analise de mercado do EchoTik/Kalodata. O mercado diz o que parece promissor. A operacao diz o que realmente funcionou nas nossas contas.

## Agentes/estruturas que ja temos

Hoje temos blocos, nao um agente unico completo:

- `gog` / Google Drive: acesso a Drive, Sheets, Docs, Gmail e Calendar.
- Google Drive connector: estrutura de pastas e planilha operacional.
- `ontology`: opcao futura para memoria estruturada de contas, produtos, tarefas e dependencias.
- `proactive-agent`: opcao futura para check-ins, lembretes e continuidade, com cautela.
- `VALIDACAO_ETAPAS.md`: define como cruzar mercado com resultado real.
- `GOOGLE_FLOW.md` e `conteudo/google-flow/`: fluxo de criacao e edicao de videos.
- `conteudo/google-flow/MULTIPLICACAO_FRAMES.md`: etapa do Gera Videos/Gera Frames, ferramenta separada que multiplica videos por frames.
- `conteudo/google-flow/INVENTARIO_LOTES_GERA_VIDEOS.md`: inventario dos lotes ja identificados na pasta `FLOW`.
- `VISAO_GERAL_PROJETO.md`: zoom out dos pilares, fluxo geral e estado atual.

Decisao: antes de criar um agente autonomo, validar a rotina em planilha + Drive.

## Abas da planilha

### `dashboard`

Resumo visual inicial:

- GMV total.
- Comissao total estimada.
- Comissao real/concluida.
- Comissao pendente.
- Saldo disponivel.
- Saldo pendente.
- Total sacado.
- Videos postados.
- Videos prontos.
- Produtos em producao.
- GMV por produto vendido.
- Tendencias 7d/14d/30d.

No primeiro momento, a aba fica como painel-base. Graficos e formulas entram depois que tivermos dados reais suficientes.

### `contas`

Controla perfis TikTok Shop:

- conta_id;
- perfil_tiktok;
- email;
- nicho;
- pais;
- status;
- meta_posts_semana;
- pasta no Drive;
- observacoes.

### `produtos`

Controla produtos selecionados:

- produto_id;
- nome;
- fonte;
- categoria;
- loja;
- preco;
- comissao;
- scores;
- decisao;
- status_producao;
- GMV/vendas/criadores/videos 30d;
- risco de compliance;
- link do relatorio local.

### `conteudo`

Controla cada video criado:

- conteudo_id;
- lote_id;
- produto_id;
- conta_id;
- variante_num;
- ambiente;
- hipotese;
- gancho;
- prompt;
- modo_geracao;
- status;
- arquivo no Drive;
- arquivo CapCut;
- arquivo final;
- checks visual/audio/compliance.

### `lotes_gera_frames`

Controla cada rodada no Gera Videos/Gera Frames, normalmente com aproximadamente 18 videos:

- lote_id;
- produto_id;
- ferramenta (`Gera Videos` ou `Gera Frames`);
- quantidade_planejada;
- quantidade_gerada;
- quantidade_aprovada;
- quantidade_descartada;
- aproveitamento;
- imagem_base_version;
- prompt_padrao_version;
- frame_set_id;
- ambiente_base;
- pasta_drive_url, apenas para armazenar o resultado exportado;
- status.

Status de lote recomendados:

```text
inventariado
pendente_limpeza_manual
pendente_revisao
pronto_para_postar
em_publicacao
postado
pausado
descartado
```

Observacao: a pasta `FLOW` contem lotes validos, mas alguns videos podem ja ter sido postados sem exclusao/movimentacao. Antes de entrar no calendario, o lote deve passar por limpeza/conferencia manual.

### `frames_prompts`

Controla os ativos que podem ser reaproveitados:

- frame_set_id;
- produto_id;
- imagem_base_version;
- prompt_padrao_version;
- ambiente;
- status;
- motivo_atualizacao;
- observacoes.

Status recomendados:

```text
gerado
aprovado
editando
final
pronto_para_postar
agendado
postado
medindo
arquivado
descartado
```

### `publicacoes`

Controla o que foi ou sera postado:

- publicacao_id;
- conteudo_id;
- produto_id;
- conta_id;
- data_programada;
- data_postada;
- status;
- video_url;
- tiktok_video_id;
- legenda;
- CTA;
- produto_vinculado.

### `metricas_diarias`

Recebe resultados da propria conta:

- data;
- conta_id;
- produto_id;
- conteudo_id;
- publicacao_id;
- views;
- cliques;
- pedidos;
- GMV;
- comissao;
- cancelamentos;
- devolucoes;
- CTR;
- conversao;
- fonte_export.

### `financeiro_conta`

Concilia dinheiro real por conta e periodo:

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

Mostra o resultado comercial por produto ja vendido:

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

Registra historico financeiro e saldos da conta:

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

### `calendario`

Organiza a rotina:

- data;
- horario;
- conta_id;
- conteudo_id;
- produto_id;
- status;
- prioridade.

### `arquivos_drive`

Mapeia os arquivos no Drive:

- drive_file_id;
- drive_url;
- nome_arquivo;
- tipo;
- produto_id;
- conteudo_id;
- status;
- pasta_atual.

### `experimentos`

Registra aprendizado:

- hipotese;
- variavel_testada;
- ambiente;
- gancho;
- resultado_24h;
- resultado_72h;
- resultado_7d;
- decisao;
- aprendizado.

### `import_tiktok`

Area de pouso para exports brutos da propria conta TikTok Shop.

### `import_echotik`

Area de pouso para dados de mercado via EchoTik/API/export.

## Pastas no Drive

Estrutura criada:

```text
TikTok Shop Operacao/
  01_produtos_validados/
  02_videos_gerados/
  03_capcut_em_edicao/
  04_prontos_para_postar/
  05_postados/
  06_descartados/
```

Regra: nao apagar videos postados. Mover para `05_postados` e registrar na aba `arquivos_drive`.

Quando um produto entrar em producao, criar uma pasta propria dentro da operacao:

```text
produto-{produto_id}_{slug}/
  00_referencias/
  01_frames_base/
  02_lotes_gerados/
  03_capcut/
  04_finais/
  05_postados/
  06_descartados/
```

## Coleta de dados da propria conta

Fontes candidatas:

1. Export manual do TikTok Shop Creator/Seller/Affiliate analytics.
2. Export de Product Analytics quando houver.
3. Export de videos/conteudos publicados.
4. API oficial/comercial, se houver acesso adequado.
5. Registro manual inicial quando export nao estiver disponivel.

Fontes oficiais consultadas:

- TikTok Shop Affiliate Seller Analytics: mostra GMV, orders, items sold, views/clicks, criadores, produtos, videos, lives, trendlines e exportacao de listas para analise offline.
- TikTok Shop Product Analytics: mostra produto/SKU, GMV, orders, units sold, funil de trafego, CTR, conversao, content ranking, creator ranking, reviews, devolucoes/reembolsos, estoque e preco.
- TikTok Shop Creator Analytics: centraliza insights de desempenho de lives, videos, produtos e usuarios para criadores.

Campos minimos:

- data;
- conta/perfil;
- video/post;
- produto;
- views;
- cliques;
- pedidos;
- GMV;
- comissao estimada;
- comissao concluida/real;
- comissao pendente;
- saldo disponivel;
- saldo pendente;
- valor sacado;
- cancelamentos/devolucoes;
- status da publicacao.

## Rotina semanal sugerida

Diario:

- Registrar lotes gerados, videos aprovados/finalizados/postados.
- Atualizar calendario do dia seguinte.
- Armazenar no Drive os outputs exportados do Gera Videos/Gera Frames e mover arquivos conforme status.

2 a 3 vezes por semana:

- Importar export do TikTok Shop.
- Atualizar metricas por video/produto.
- Atualizar financeiro real: GMV, comissao concluida, pendente, saldo e saques.
- Revisar produtos com melhor resposta.

Semanal:

- Fechar ranking de produtos.
- Ver top videos por comissao/GMV.
- Conciliar comissao estimada vs comissao real/concluida.
- Ver top produtos por GMV vendido e por dinheiro realmente recebido.
- Decidir quais produtos continuam, pausam ou escalam.
- Atualizar aprendizados em `experimentos`.

## Proximas automacoes

1. Mapear arquivos atuais do Drive e preencher `arquivos_drive`.
2. Criar primeiras linhas em `produtos` a partir das analises locais.
3. Criar primeiras linhas em `lotes_gera_frames` para os lotes ja produzidos no Gera Videos/Gera Frames.
4. Marcar lotes vindos da pasta `FLOW` como `pendente_limpeza_manual` quando puderem conter videos ja postados.
5. Criar primeiras linhas em `conteudo` a partir dos videos ja existentes.
6. Definir padrao de `lote_id`, `conteudo_id` e `publicacao_id`.
7. Importar primeiro export real do TikTok Shop para `import_tiktok`.
8. Normalizar esse export para `metricas_diarias`.
9. Criar abas financeiras: `financeiro_conta`, `vendas_produto` e `saldos_tiktok`.
10. Conciliar comissao estimada, comissao concluida, saldo disponivel e saques.
11. Criar formulas/graficos no `dashboard`.

## Regras de seguranca

- Nao publicar, alterar conta ou apagar arquivos sem confirmacao explicita.
- Nao guardar senhas, tokens, cookies ou codigos 2FA na planilha.
- Dados sensiveis de conta devem ficar somente no Drive/Sheets autorizados.
- Coletas automatizadas devem registrar fonte, data e qualidade do dado.
