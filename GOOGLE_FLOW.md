# Google Flow - uso no projeto TikTok Shop

Fonte lida em 2026-07-16: documentacao oficial do Google Flow Help.

Objetivo neste projeto: usar Google Flow como camada de criacao e edicao de videos para produtos que ja passaram por validacao de mercado, comissao, estoque e compliance.

## Onde entra no fluxo

Google Flow entra depois da decisao de produto:

1. Produto analisado no EchoTik/Kalodata/TikTok Shop.
2. Produto aprovado como `testar` ou `produzir`.
3. Criar briefing de conteudo: promessa permitida, angulo, avatar, prova, CTA e restricoes.
4. Criar assets: imagem do produto, frames, referencia visual, roteiro curto e cenas.
5. Gerar ou editar clipes no Google Flow.
6. Montar sequencia no Scenebuilder.
7. Exportar video e registrar no historico do produto.
8. Medir resultado real no TikTok Shop e recalibrar score.

## Capacidades uteis

- Criar videos por prompt de texto.
- Animar imagens e usar frames de inicio/fim.
- Usar ingredientes/referencias visuais para manter consistencia de produto, objeto, personagem ou estilo.
- Editar videos enviados ou gerados, com prompt de alteracao.
- Trabalhar com clipes curtos e montar sequencias no Scenebuilder.
- Salvar frames como imagens para reutilizar como ingrediente, start frame ou end frame.
- Usar Agent para pedir variacoes de uma mesma ideia.
- Criar Tools internas no Google Flow para tarefas repetitivas, com cuidado porque podem consumir creditos.

## Limites importantes

- Google Flow exige conta elegivel, regiao suportada e, dependendo do caso, plano Google AI Plus/Pro/Ultra ou creditos diarios/free.
- Desktop com navegador Chromium e o melhor caminho para edicao mais completa.
- Alguns recursos dependem do modelo e da regiao.
- Video upload/edit: arquivos ate 60 segundos e 1 GB, formatos `.mov`, `.mp4`, `.avi`, `.wmv`.
- Se o video enviado tiver mais de 30 segundos, precisa ser cortado para 30 segundos no Flow.
- A edicao trabalha com segmentos de ate 10 segundos.
- Alguns fluxos permitem ate 3 turnos conversacionais mantendo contexto.
- Outputs gerados por Veo/Imagen incluem SynthID; videos podem ter marca d'agua visivel dependendo do plano/regiao.
- Creditos variam por modelo e plano; verificar custo antes de gerar.

## Regras de seguranca e privacidade

- Nao enviar informacao confidencial, credenciais, dados privados de cliente, resultados internos sensiveis ou estrategia proprietaria desnecessaria para prompts do Google Flow.
- Desativar ou revisar a opcao `Help improve Google Flow` se formos trabalhar com conteudo sensivel.
- Tratar uploads, prompts e outputs como dados que podem ser usados conforme politicas do Google Flow.
- Nao usar fotos reais de pessoas, voz, avatar ou likeness sem permissao.
- Para suplementos, saude, beleza e claims sensiveis: gerar videos sem promessa medica, sem antes/depois enganoso e sem afirmacoes nao comprovadas.
- Registrar `prompt`, `produto_id`, `assets_usados`, `modelo`, `creditos_estimados`, `output_file`, `decisao_editorial` e `restricoes_compliance`.

## Template de briefing para Flow

```text
Produto:
ID/fonte:
Categoria:
Oferta/SKU:
Comissao confirmada:
Restricoes de compliance:
Angulo principal:
Gancho:
Cena 1:
Cena 2:
Cena 3:
CTA:
Formato:
Duracao:
Referencia visual/ingredientes:
Nao fazer:
```

## Prompt base para produto

```text
Create a short vertical TikTok Shop product video for [PRODUCT].
Use a clean, realistic, UGC-style visual.
Show the product clearly in the first seconds.
Focus on [ANGLE] without making medical, exaggerated, or unverified claims.
Scene 1: [HOOK VISUAL].
Scene 2: [PRODUCT DEMONSTRATION].
Scene 3: [OFFER/CTA].
Keep it natural, direct, and suitable for a Brazilian TikTok Shop affiliate video.
```

## Decisao operacional

Google Flow deve ser tratado como ferramenta de producao, nao como fonte de decisao de produto.

Ele nao substitui a analise de mercado. Ele transforma produtos aprovados em assets de teste: variacoes de gancho, cenas, frames, demonstracoes e cortes para validacao real.

## Validacao de automacao

Arquivo operacional:

- `VALIDACAO_GOOGLE_FLOW.md`

Primeiro piloto:

- `conteudo/google-flow/piloto_tetra_magnesio_1732849645859341976.md`

Workflow atual do usuario:

- `conteudo/google-flow/WORKFLOW_ATUAL_USUARIO.md`

Revisao economica de audio/video:

- `conteudo/google-flow/REVISAO_ECONOMICA_AUDIO_VIDEO.md`

Regra: usar `low priority` como caminho padrao quando nao consumir credito. Antes de automatizar login, upload, geracao paga/rapida com creditos, exportacao ou publicacao, validar manualmente um ciclo completo com registro de prompt, output, modo de geracao, qualidade e compliance.
