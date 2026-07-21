# Validacao Google Flow - geracao e edicao de conteudo

Data de criacao: 2026-07-16  
Objetivo: validar ate onde o fluxo de criacao/edicao de conteudo no Google Flow pode ser automatizado sem perder controle de custo, qualidade, compliance e aprendizado.

## Pergunta de validacao

```text
Conseguimos transformar um produto aprovado em videos testaveis com processo repetivel, rastreavel e parcialmente automatizavel?
```

## Escopo do primeiro teste

- 1 produto ja analisado.
- 3 hipoteses criativas.
- 3 prompts principais.
- 1 variacao/edit por prompt.
- Exportar ou registrar os outputs, mesmo que a publicacao ainda seja manual.
- Medir se o processo gerou assets utilizaveis para TikTok Shop.
- Validar tambem o fluxo real do usuario: referencia de ambiente, mudanca de ambiente, prompts padrao, filtro de artefatos, download, CapCut, arquivo final e publicacao.

Produto recomendado para o piloto:

- `1732849645859341976` - TETRA MAGNESIO.

Motivo:

- Tem demanda forte validada.
- Tem videos vencedores na fonte.
- Exige cuidado de compliance, o que testa se nosso processo evita claims arriscados.

Workflow atual registrado:

- `conteudo/google-flow/WORKFLOW_ATUAL_USUARIO.md`

Revisao economica de qualidade:

- `conteudo/google-flow/REVISAO_ECONOMICA_AUDIO_VIDEO.md`

Gera Videos / Gera Frames:

- `conteudo/google-flow/MULTIPLICACAO_FRAMES.md`
- `conteudo/google-flow/INVENTARIO_LOTES_GERA_VIDEOS.md`

## Workflow real do usuario

```text
Imagem de ambiente como referencia
> mudar apenas o ambiente
> aplicar prompts padrao ja validados para gerar frames
> filtrar interferencias, inconsistencias de audio, deformacoes e simbolos
> baixar outputs aprovados
> subir no CapCut
> remover ruidos sonoros e cortar fim de cena/fala
> salvar arquivo final
> usar Gera Videos/Gera Frames para multiplicar variacoes por frames
> armazenar no Drive o lote exportado do Gera Videos/Gera Frames
> identificar padrao G/C/A no nome dos arquivos
> limpar manualmente os videos ja postados antes de agendar
> repetir com 2 ambientes por produto
> edicoes finais
> publicar no TikTok Shop
```

Hipotese de automacao:

```text
Automatizar preparacao, geracao low priority sem credito e registro primeiro.
Manter upload sensivel, download, CapCut e publicacao com confirmacao humana ate validar 5 produtos / 10 videos.
```

## Niveis de automacao

### Nivel 0 - Manual controlado

Uso:

- Produto aprovado manualmente.
- Briefing escrito manualmente ou assistido por Codex.
- Prompts colados manualmente no Google Flow.
- Download/export manual.
- Resultado registrado manualmente.

Automatizar:

- Criacao do briefing.
- Criacao de prompts.
- Checklist de compliance.
- Nomenclatura de arquivos.
- Registro em Markdown/planilha.

Nao automatizar:

- Login.
- Geracao paga/rapida que consome credito.
- Upload de arquivos sensiveis.
- Publicacao no TikTok.

### Nivel 1 - Assistido por agente

Uso:

- Codex gera pacote de prompts, hipoteses, cenas e criterios de avaliacao.
- Usuario faz login e aprova geracao.
- Codex pode ajudar a organizar nomes, registrar outputs e comparar variacoes.

Automatizar:

- Pacote de 3 a 5 prompts por produto.
- Variacoes por angulo: gancho, prova, oferta, demonstracao, CTA.
- Variacoes por ambiente: 2 ambientes por produto.
- Transformacao de prompts padrao em prompts finais por ambiente.
- Checklist de filtro visual/audio antes de baixar e editar.
- Registro de prompt e output.
- Score editorial pos-geracao.

Exigir confirmacao:

- Antes de usar modo pago/rapido que consome creditos.
- Antes de enviar qualquer imagem/video para Flow.
- Antes de salvar arquivos em Drive ou compartilhar.

### Nivel 2 - Browser assistido

Uso:

- Codex abre Flow em perfil isolado ou navegador controlado.
- Usuario autentica.
- Codex pode preencher prompt e organizar etapas, com confirmacao antes de acoes sensiveis.

Automatizar com cautela:

- Abrir projeto.
- Colar prompt.
- Selecionar assets nao sensiveis.
- Disparar geracao `low priority` quando nao consumir credito e usar assets previamente aprovados.
- Registrar output.
- Tirar screenshot de resultado.

Confirmar sempre:

- Upload.
- Geracao paga/rapida que consome credito.
- Aceitar termos/permissoes.
- Download/export.
- Alteracao de configuracoes de privacidade.

### Nivel 3 - Automacao recorrente

Uso futuro, somente se os niveis 0 a 2 forem validados.

Pre-condicoes:

- Usar `low priority` como padrao quando nao consumir credito.
- Limite de creditos por dia para qualquer modo pago/rapido.
- Padrao de naming.
- Planilha/tabela de experimentos.
- Criterios objetivos de aprovado/reprovado.
- Processo de compliance por categoria.
- Confirmacao do usuario para cada lote.

Nao iniciar agora.

## O que medir

### Processo

| Metrica | Meta inicial |
|---|---:|
| Tempo do briefing ao primeiro output | ate 20 min |
| Prompts gerados por produto | 3 a 5 |
| Ambientes por produto | 2 |
| Videos planejados por lote | aproximadamente 18 |
| Variacoes uteis por produto | pelo menos 1 por ambiente |
| Aproveitamento do lote | registrar aprovados / gerados |
| Disponibilidade real do lote | confirmar se nao ha videos ja postados |
| Retrabalho por video | no maximo 2 edicoes |
| Modo de geracao | registrar `low priority`, pago ou outro |
| Creditos por video utilizavel | registrar somente quando houver custo |
| Tempo de CapCut por video | registrar baseline |

### Qualidade criativa

| Criterio | Pergunta |
|---|---|
| Clareza do produto | O produto aparece claro nos primeiros segundos? |
| Gancho | O primeiro frame/frase da vontade de continuar? |
| Demonstracao | O video mostra uso/oferta sem confundir? |
| Naturalidade | Parece conteudo TikTok/UGC ou propaganda artificial? |
| CTA | O proximo passo fica claro? |
| Reuso | O asset pode virar outro corte/variante? |
| Ambiente | A mudanca de ambiente gerou video realmente diferente? |

### Filtro de artefatos

| Criterio | Pergunta |
|---|---|
| Deformacoes | Produto, maos, objetos ou embalagem ficaram deformados? |
| Simbolos | Apareceu simbolo, logo, texto estranho ou marca inventada? |
| Audio | Ha ruido, fala cortada, voz artificial demais ou silencio estranho? |
| Continuidade | A cena termina junto com a fala ou precisa corte no CapCut? |
| Ambiente | O ambiente novo combina com o produto e nao distrai? |

### Revisao economica

O fluxo de revisao deve minimizar tokens:

1. Rodar filtro tecnico local sem GPT.
2. Extrair poucos frames-chave por video.
3. Revisar frames com checklist curto em JSON.
4. Descartar outputs com erro estrutural antes do CapCut.
5. Usar `gpt-5.6-sol` apenas para decisao estrategica, nao para triagem de rotina.

Regra inicial:

```text
No maximo 4 a 8 frames por video para revisao visual automatizada.
```

### Compliance

| Criterio | Pergunta |
|---|---|
| Claims | Ha promessa medica, exagerada ou nao comprovada? |
| Categoria sensivel | O roteiro respeita restricoes de saude/suplementos? |
| Likeness | Usa pessoa real, voz ou imagem sem permissao? |
| Privacidade | Envia dado sensivel desnecessario ao Flow? |
| Transparencia | O conteudo evita enganar sobre resultado do produto? |

### Resultado real

Depois de publicar manualmente:

- Views 24h, 72h, 7d.
- Cliques.
- Pedidos.
- GMV.
- Comissao.
- Comentarios com duvida/objeção.
- Video aprovado/reprovado pela plataforma.

## Campos para registrar por output

```text
produto_id:
produto_nome:
categoria:
hipotese:
angulo:
ambiente:
referencia_ambiente:
lote_id:
variante_num:
prompt:
assets_usados:
modelo:
modo_geracao:
creditos_estimados:
tempo_producao:
output_file:
arquivo_capcut:
arquivo_final:
status_editorial:
status_compliance:
observacoes:
publicado:
video_url:
metricas_24h:
metricas_72h:
metricas_7d:
decisao_pos_teste:
```

## Decisao sobre automacao

So avancar para maior automacao se:

- pelo menos 1 output de 3 for publicavel;
- o modo `low priority` gerar qualidade aceitavel ou o custo em creditos for justificavel;
- o processo nao criar risco de claim/compliance;
- o registro de prompts e outputs for suficiente para aprendizado;
- o usuario aprovar que a qualidade visual serve para TikTok Shop.
- 2 ambientes por produto gerarem diferenca visual suficiente sem perder consistencia.
- a revisao economica detectar erros obvios sem consumir tokens demais.

Se a qualidade visual for boa, mas o processo for lento, automatizar preparacao e registro.

Se a qualidade visual for ruim, usar Flow apenas para frames, b-roll, ideias ou rascunhos.

Se o risco de compliance for alto, limitar Flow a categorias nao sensiveis primeiro.
