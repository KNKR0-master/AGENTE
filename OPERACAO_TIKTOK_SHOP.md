# Operacao TikTok Shop

Data de criacao: 2026-07-21  
Ultima leitura do Drive/Sheets: 2026-08-08

Objetivo: organizar a rotina das contas TikTok Shop, os produtos em producao, o estoque de videos, a programacao de postagens e os resultados reais da conta.

## Estrutura criada

## Recontagem diaria — 2026-08-06

No recorte das 21 linhas de `estoque_videos`, a leitura recursiva das pastas registrou 402 vídeos válidos em 30 pastas. A regra estrita conta somente arquivos de vídeo com `G<number>C<number>A<number>` ou `FINAL` no nome, deduplicados por ID; a coluna da planilha é apenas referência. Dez produtos ficaram abaixo de 8 vídeos válidos.

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

Leitura atual da planilha-mae em 2026-07-28:

- Abas reais observadas: 18.
- `produtos`: 2 produtos com analise EchoTik registrada.
- `conteudo`: 1 piloto registrado para Tetra Magnesio.
- `pipeline_ceci`: 23 produtos em andamento.
- `pipeline_fe`: 10 produtos em andamento.
- `estoque_videos`: 21 produtos/pastas, 303 videos em contagem bruta anterior.
- `calendario_postagens`: 21 linhas de planejamento, horizonte 2026-07-22 a 2026-08-04.
- `publicacoes`, `metricas_diarias`, `arquivos_drive`, `experimentos`, `import_tiktok`, `import_echotik`, `lotes_gera_frames` e `frames_prompts`: apenas cabecalhos, sem linhas de dados nesta leitura.
- `financeiro_conta`, `vendas_produto` e `saldos_tiktok`: planejadas na documentacao, mas ainda nao existem como abas reais observadas.

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

### Pipelines por perfil

Controlam os produtos que estao girando em cada perfil, sem misturar producao de conteudo com venda/financeiro.

Abas atuais:

- `pipeline_ceci`: perfil `Ceci`.
- `pipeline_fe`: perfil `Fe`.

Estrutura:

- perfil;
- produto_nome;
- produto_normalizado;
- marcador_lista (`OK GERA`, `NOVO`, `ANTIGO` ou vazio);
- status_validacao;
- status_gera_viral;
- status_criativo;
- status_venda;
- status_financeiro;
- drive_status;
- proxima_acao;
- observacoes.

Regra: `OK GERA` indica producao/multiplicacao no Gera Viral, nao venda. `NOVO` indica produto validado que ainda precisa modelagem, roteiro e/ou imagens. Produtos sem `OK GERA` ainda podem estar vendendo ou ja ter vendido; essa confirmacao deve vir das abas financeiras/imports do TikTok Shop.

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

### `estoque_videos`

Snapshot de videos disponiveis por produto/pasta:

- data_snapshot;
- fonte;
- produto_id;
- produto_nome;
- perfil_sugerido;
- videos_disponiveis;
- ritmo_postagens_dia;
- dias_cobertura;
- tipo_estoque;
- pasta_drive_url;
- observacoes.

Regra: esta aba conta disponibilidade operacional, nao venda nem publicacao. Arquivos continuam no Drive e nao devem ser movidos, apagados ou renomeados sem autorizacao.

Regra revisada de contagem de estoque:

- A coluna `videos_disponiveis` pode estar inconsistente quando representar apenas quantidade bruta de `.mp4`.
- Para estoque publicavel, contar somente arquivos de video cujo nome contenha:
  - padrao Gera Viral/Gera Frames `G<number>C<number>A<number>`, por exemplo `G1C2A3`; ou
  - a palavra `FINAL`, sem diferenciar maiusculas/minusculas.
- Ignorar outros `.mp4`, rascunhos, previews, arquivos antigos e qualquer video sem `G<number>C<number>A<number>` ou `FINAL`.
- Quando houver subpastas, a automacao deve contar tambem os arquivos elegiveis dentro delas, sem mover ou apagar nada.

### `calendario_postagens`

Matriz diaria de postagem por produto. O primeiro calendario foi criado em 2026-07-22 com a regra:

```text
2 videos por produto com estoque disponivel por dia
```

A matriz usa uma linha por produto e uma coluna por data. Cada celula indica quantos videos daquele produto devem ser postados naquele dia. Quando o estoque e impar, o ultimo dia fica com 1 video.

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
- Conferir `estoque_videos` e `calendario_postagens` pela automacao diaria, mantendo a regra de somente leitura no Drive.

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

## Snapshot estrito de 2026-07-30

- 21 produtos/pastas lidos com sucesso no Drive; nenhuma pendência de permissão.
- 193 vídeos válidos pela regra estrita, deduplicados por ID e incluindo subpastas aplicáveis.
- Itens abaixo de 8 vídeos válidos devem ser priorizados para nova produção: BS Arabe Amber Royale, BS Arabe Masc Kit 3, Hemorroida, Hidrabene (protetor e kits), Body Splash Ayra, Escorredor, Kit Pano Microfibra, BS Isly, Kit Calca Infantil e Florat Red Egeu.
- A leitura é somente inventário: não houve publicação, movimentação, exclusão ou escrita na planilha.

## Snapshot estrito de 2026-07-29

- 21 produtos/pastas lidos com sucesso no Drive; nenhuma pendencia de permissao.
- 188 videos validos pela regra estrita, contra 303 `.mp4`/videos da contagem bruta registrada na planilha.
- O alerta diario deve priorizar os produtos abaixo de 8 videos validos: Hemorroida, Hidrabene (protetor e kits), Body Splash Ayra, Escorredor, Kit Pano Microfibra, BS Isly, Kit Calca Infantil e Florat Red Egeu.
- A leitura e somente inventario: nao houve publicacao, movimentacao, exclusao ou escrita na planilha.

1. Recontar `estoque_videos` pela regra estrita: somente nomes com `G<number>C<number>A<number>` ou `FINAL`.
2. Marcar lotes vindos da pasta `FLOW` como `pendente_limpeza_manual` quando puderem conter videos ja postados.
3. Criar primeiras linhas em `conteudo` a partir dos videos ja existentes ou dos lotes revisados.
4. Definir conta/perfil para `ESCOVA ELETRICA`, `BS ARABE AYRA + AMBER KIT` e `BODY SPLASH AGAPIS`.
5. Mapear arquivos atuais do Drive e preencher `arquivos_drive`.
6. Manter `pipeline_ceci` e `pipeline_fe` atualizados com status de Gera Viral, criativo, venda e financeiro.
7. Definir padrao de `lote_id`, `conteudo_id` e `publicacao_id`.
8. Importar primeiro export real do TikTok Shop para `import_tiktok`.
9. Normalizar esse export para `metricas_diarias`, `publicacoes` e resultado por produto.
10. Criar abas financeiras reais: `financeiro_conta`, `vendas_produto` e `saldos_tiktok`.
11. Conciliar comissao estimada, comissao concluida, saldo disponivel e saques.
12. Criar formulas/graficos no `dashboard`.

## Regras de seguranca

- Nao publicar, alterar conta ou apagar arquivos sem confirmacao explicita.
- Nao guardar senhas, tokens, cookies ou codigos 2FA na planilha.
- Dados sensiveis de conta devem ficar somente no Drive/Sheets autorizados.
- Coletas automatizadas devem registrar fonte, data e qualidade do dado.

## Recontagem diaria — 2026-08-08

No recorte das 21 linhas de `estoque_videos`, a leitura recursiva das pastas registrou 308 videos validos em 31 pastas lidas (21 raizes e 10 subpastas). A regra estrita conta somente arquivos de video com `G<number>C<number>A<number>` ou `FINAL` no nome, deduplicados por ID; a coluna da planilha e apenas referencia. Dez produtos ficaram abaixo de 8 videos validos.
