# Snapshot Drive/Sheets - Operacao TikTok Shop

Data de leitura: 2026-07-28  
Fonte principal: Google Drive / `Operacao TikTok Shop - Planilha Mae`  
Planilha: `https://docs.google.com/spreadsheets/d/12wEBjSpfBBqm0Zj4sncafhjnGnt6SH_YWZUtbQDIq48/edit`

## Resumo executivo

- A pasta `TikTok Shop Operacao` existe com 6 subpastas operacionais: produtos validados, videos gerados, CapCut em edicao, prontos para postar, postados e descartados.
- A planilha-mae foi criada em 2026-07-21 e modificada em 2026-07-24.
- A estrutura atual tem 18 abas reais.
- `financeiro_conta`, `vendas_produto` e `saldos_tiktok` ainda estao planejadas na documentacao, mas nao apareceram como abas reais nesta leitura.
- Ha 2 produtos de mercado registrados na aba `produtos`.
- Ha 33 produtos em pipelines por perfil: 23 em `pipeline_ceci` e 10 em `pipeline_fe`.
- Ha 21 linhas de estoque de videos, somando 303 videos na contagem bruta anterior da planilha.
- A contagem deve ser recalculada pela regra revisada: somente arquivos de video com `G<number>C<number>A<number>` ou `FINAL` no nome contam como estoque.
- O calendario de postagens cobre o horizonte de 2026-07-22 a 2026-08-04, usando ritmo base de 2 videos por produto/dia.
- Abas de publicacoes, metricas, arquivos, experimentos, import TikTok, import EchoTik, lotes e frames ainda estao sem linhas de dados alem dos cabecalhos.

## Abas observadas

| Aba | Linhas de dados observadas | Papel |
|---|---:|---|
| `README` | 5 linhas informativas | Instrucoes e regra de uso. |
| `dashboard` | 5 metricas-base | Painel-base sem formulas/resultados reais preenchidos. |
| `contas` | 0 | Cadastro de perfis. |
| `produtos` | 2 | Produtos analisados via EchoTik. |
| `conteudo` | 1 | Piloto de conteudo para Tetra Magnesio. |
| `publicacoes` | 0 | Posts programados/publicados. |
| `metricas_diarias` | 0 | Resultado real por publicacao/produto. |
| `calendario` | 0 | Calendario operacional granular. |
| `arquivos_drive` | 0 | Mapa de arquivos. |
| `experimentos` | 0 | Aprendizados. |
| `import_tiktok` | 0 | Pouso de export TikTok Shop. |
| `import_echotik` | 0 | Pouso de export/API EchoTik. |
| `lotes_gera_frames` | 0 | Rodadas Gera Videos/Gera Frames. |
| `frames_prompts` | 0 | Frame sets, imagem base e prompts. |
| `pipeline_ceci` | 23 | Produtos girando no perfil Ceci. |
| `pipeline_fe` | 10 | Produtos girando no perfil Fe. |
| `estoque_videos` | 21 | Estoque de videos por produto/pasta. |
| `calendario_postagens` | 21 | Plano de postagem por produto/data. |

## Produtos em `produtos`

| Produto | Fonte | Decisao | Status | Market score | Priority score | Risco |
|---|---|---|---|---:|---:|---|
| Kit 6 Travesseiro Sintetico Descanso Antiacaro 70x50cm Luxo | EchoTik | monitorar | watchlist | 60,5 | 61,9 | medio - rating baixo e estoque |
| TETRA MAGNESIO - 4 Magnesios | EchoTik | testar se comissao confirmar | candidato_teste | 66 | 64,8 | alto - suplemento/claims |

## Pipeline Ceci

Total observado: 23 produtos.

Produtos com `OK GERA` ou `NOVO + OK GERA`, ou seja, conteudo produzido/multiplicado no Gera Viral:

- Kit 2 travesseiros NOVO.
- hidrabene kit.
- Escorredor.
- protetor solar hidrabene.
- KIT calcas infantil.
- kit pano microfibra.
- cebo de carneiro.
- body splash ARABE AYRA.
- kit 6 bodysplash SLY PURE NATURE.
- KIT hidrantes hidrabene body cream 4 frag.
- Kit BodySplash+BodySplash Mini 50ml+Body Cream HIDRABENE.

Produtos `NOVO`, ainda pedindo modelagem/roteiro/imagens:

- Kreain Nature 4 Spray Higienizante Nasal.
- body splash feminino arabe amber royale.
- 3 PARES MEIA BICHINHO SOQUETE.
- BODY SPLASH ARABE MASCULINO KIT C/3.
- Hidrabene Base Facial Multifuncao Stick.
- Kit 6 Refis Body Splashes: Perigo, Coragem, etc.

Outros produtos no pipeline sem `OK GERA`:

- BODY SPLASH MASCULINO KIT C/2 BARBOUS.
- kit 2 travesseiros ANTIGO.
- body splash FLORATE RED & EGEU DOLCE.
- KIT 2 CREMES HIVEN WAVES.
- kit 6 travesseiros.
- Kit 6 Body Splash Barbour's Beauty 200ml.

## Pipeline Fe

Total observado: 10 produtos.

Produto com `OK GERA`:

- Kreain Nature Gel para Pele Sensivel | Textura Leve e Secagem Rapida HEMORROIDA.

Produtos `NOVO`, ainda pedindo modelagem/roteiro/imagens:

- Cola para Metal AB 100g Solda Fria Reparo Industrial Resistente ao Calor Ferro Aco Aluminio.
- SUPLEMENTO NATURAL MONJAR NATURAL.
- ILUMINADOR DE VIRILHAS E AXILAS - LAVID.

Outros produtos no pipeline:

- PRO 3 MAGNESIO.
- Complexo B.
- Cebo de carneiro.
- Kit pro 3 magnesio.
- maquininha.
- tetra magnesio.

## Estoque de videos

Total bruto anterior: 21 produtos/pastas, 303 videos.

Importante: estes numeros vieram da coluna/contagem anterior da planilha. Para alerta operacional, o estoque valido deve ser recontado diretamente nas pastas do Drive, considerando apenas nomes com `G<number>C<number>A<number>` ou `FINAL`.

| Produto/pasta | Perfil | Videos | Cobertura | Observacao |
|---|---|---:|---:|---|
| ESCOVA ELETRICA | pendente_conta | 24 | 12 dias | Confirmar perfil antes de publicar. |
| BS ARABE AMBER ROYALE | Ceci | 12 | 6 dias | Contagem revisada. |
| BS ARABE MASC KIT 3 | Ceci | 12 | 6 dias | Contagem revisada. |
| HEMORROIDA / KREAIN NATURE | Fe | 10 | 5 dias | Contagem revisada. |
| HIDRABENE / PROTETOR HIDRABENE | Ceci | 8 | 4 dias | Contagem revisada. |
| HIDRABENE / KIT BS + CREME | Ceci | 10 | 5 dias | Contagem revisada. |
| HIDRABENE / KIT 4 BODY CREAM | Ceci | 10 | 5 dias | Contagem revisada. |
| HIDRABENE / KIT HIDRABENE | Ceci | 10 | 5 dias | Contagem revisada. |
| BODY SPLASH AYRA | Ceci | 10 | 5 dias | Contagem revisada. |
| KIT 2 TRAVESSEIROS | Ceci | 24 | 12 dias | 24 MP4 em duas subpastas. |
| ESCORREDOR | Ceci | 10 | 5 dias | Contagem revisada. |
| CEBO DE CARNEIRO / CREME MULHER | Ceci | 38 | 19 dias | 28 MP4 novos + 10 em subpasta mulher. |
| KIT PANO MICROFIBRA | Ceci | 7 | 4 dias | Ultimo dia fica com 1 video. |
| BS ISLY FEM KIT6 | Ceci | 10 | 5 dias | Subpasta GERAVIRAL. |
| KIT CALCA INFANTIL | Ceci | 10 | 5 dias | Subpasta geraviral/VIDEOS. |
| PRO 3 MAGNESIO | Fe | 28 | 14 dias | Novo lote identificado em 2026-07-23. |
| MAQUININHA | Fe | 28 | 14 dias | Novo lote identificado em 2026-07-23. |
| BODY SPLASH FLORAT RED EGEU | Ceci | 6 | 3 dias | MP4 antigos na subpasta 11-07. |
| BS ARABE AYRA + AMBER KIT | pendente_perfil | 14 | 7 dias | Confirmar perfil antes de publicar. |
| BODY SPLASH AGAPIS | pendente_perfil | 6 | 3 dias | 6 MP4 finais; confirmar perfil. |
| BS BARBOUR'S MASC KIT2 | Ceci | 16 | 8 dias | Novo estoque; lote Gera Viral em subpasta gera. |

## Lacunas atuais

- Nao ha dados reais nas abas `publicacoes`, `metricas_diarias`, `arquivos_drive` e `experimentos`.
- `import_tiktok` e `import_echotik` seguem vazias.
- `lotes_gera_frames` e `frames_prompts` ainda nao foram populadas apesar de haver estoque inventariado.
- Quantidades de estoque precisam de recontagem estrita por padrao de nome antes de gerar alertas ou calendario.
- O financeiro real ainda precisa de abas efetivas ou importacao para entrar no painel.
- Produtos com `OK GERA` nao devem ser tratados como vendidos; indicam apenas producao/multiplicacao.
- Produtos com perfil `pendente_conta` ou `pendente_perfil` precisam de decisao antes de publicar.

## Proxima acao recomendada

1. Popular `lotes_gera_frames` a partir das 21 linhas de `estoque_videos`.
2. Popular `conteudo` com pelo menos uma linha por video aprovado ou por lote revisado.
3. Definir conta/perfil dos produtos pendentes antes de usar o calendario.
4. Importar export real do TikTok Shop em `import_tiktok`.
5. Normalizar publicacoes e metricas reais para fechar o ciclo de decisao.
