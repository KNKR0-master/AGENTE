# Revisao economica de audio e video

Data de registro: 2026-07-17

Objetivo: mitigar erros breves de audio/video gerados no Google Flow sem gastar muitos tokens e sem depender de revisao humana cansativa em todos os detalhes.

## Principio

GPT nao deve assistir tudo em alta profundidade.

A revisao deve funcionar em camadas:

1. Filtro tecnico local, sem GPT.
2. Extracao de poucos frames-chave.
3. Revisao visual barata com checklist fechado.
4. Revisao humana apenas dos casos duvidosos ou candidatos finais.
5. `gpt-5.6-sol` somente para estrategia, nao para triagem operacional.

## Camada 1 - Filtro tecnico local

Sem consumo de tokens.

Usar ferramentas locais, como `ffmpeg` e scripts, para detectar:

- duracao fora do esperado;
- resolucao ou aspect ratio errado;
- audio muito baixo ou estourado;
- silencio estranho;
- trecho sem audio quando deveria haver fala;
- cortes abruptos;
- frames congelados;
- video quase sem movimento;
- arquivo corrompido;
- taxa de frames instavel.

Saida esperada:

```text
video_file:
duration:
resolution:
audio_peak:
audio_silence_flags:
freeze_flags:
scene_cut_flags:
technical_status:
```

## Camada 2 - Frames-chave

Em vez de mandar o video inteiro para revisao, extrair poucos frames:

- frame inicial;
- frame em 25%;
- frame em 50%;
- frame em 75%;
- frame final;
- frames em mudancas de cena, se detectadas.

Regra inicial:

```text
Enviar no maximo 4 a 8 frames por video para revisao visual.
```

## Camada 3 - Revisao visual barata

Usar modelo mais barato ou intermediario, como `gpt-5.6-luna` ou `gpt-5.6-terra`, com prompt curto e saida JSON.

Nao usar `gpt-5.6-sol` para triagem padrao.

Checklist:

- embalagem ilegivel;
- produto deformado;
- texto estranho;
- simbolo ou logo inventado;
- mao/dedo deformado;
- objeto flutuando;
- ambiente incoerente;
- claim sensivel;
- cena que nao combina com produto;
- risco de reprovar na publicacao.

Prompt padrao:

```text
Review these keyframes from a short TikTok Shop product video.
Use the checklist only.
Return compact JSON.

Reject if there is: unreadable product package, distorted product, strange text,
invented symbols/logos, deformed hands, floating objects, incoherent setting,
medical or exaggerated claims, or anything unsafe for TikTok Shop.

JSON fields:
approved, severity, issues, suggested_action.
```

## Camada 4 - Decisao de descarte rapido

Se um output falhar por problema obvio, descartar sem gastar mais tokens:

- embalagem ilegivel;
- deformacao forte;
- audio inutil;
- produto errado;
- ambiente incoerente;
- claim sensivel;
- cena visualmente confusa.

Nao enviar para CapCut quando o problema for estrutural.

## Quando um prompt gera zero videos uteis

Registrar a falha, nao insistir as cegas.

```text
prompt_id:
produto_id:
ambiente:
falha_principal:
acao_corretiva:
status:
```

Falhas comuns e acoes:

| Falha | Acao corretiva |
|---|---|
| Embalagem ilegivel | Produto mais estatico, close-up simples, menos texto. |
| Maos deformadas | Reduzir interacao de maos, usar produto parado. |
| Audio ruim | Gerar sem audio ou usar audio no CapCut. |
| Simbolos estranhos | Proibir texto/logos extras e simplificar ambiente. |
| Cena confusa | Reduzir numero de objetos e usar uma acao por cena. |
| Claim sensivel | Trocar para leitura de rotulo/rotina neutra. |

## Prompt corretivo

Usar somente quando a falha for clara.

```text
Problem detected: [ISSUE].
Regenerate the clip with a simpler scene.
Keep the product static and clearly visible.
Avoid small readable text, extra symbols, invented logos, distorted hands,
and any medical or exaggerated claim.
Use the same product and environment idea, but reduce visual complexity.
```

## Politica de modelos

| Tarefa | Modelo recomendado |
|---|---|
| Naming, registro, checklist simples | `gpt-5.6-luna` |
| Revisao de frames-chave | `gpt-5.6-terra` |
| Prompt corretivo simples | `gpt-5.6-terra` |
| Decisao estrategica de escalar produto/criativo | `gpt-5.6-sol` |

## Regra de eficiencia

Para cada lote:

1. Todos os videos passam por filtro tecnico local.
2. Apenas candidatos sem erro tecnico grave passam por frames-chave.
3. Frames-chave passam por revisao JSON curta.
4. Videos reprovados sao descartados ou regenerados com prompt corretivo.
5. Videos aprovados vao para CapCut.
6. Apenas finalistas recebem revisao humana detalhada.

## Metricas de aprendizado

Registrar por ambiente/prompt:

- quantos outputs foram gerados;
- quantos reprovaram por audio;
- quantos reprovaram por imagem;
- quantos foram para CapCut;
- quantos viraram final;
- quantos foram publicados;
- resultado 24h/72h/7d.

Depois de 5 produtos / 10 videos finais, revisar:

- quais ambientes geram menos erros;
- quais prompts geram mais videos uteis;
- quais falhas mais aparecem;
- quais regras devem entrar no prompt base;
- se vale automatizar mais a revisao visual.
