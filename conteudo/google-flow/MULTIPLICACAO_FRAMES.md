# Gera Videos / Gera Frames - multiplicacao de videos por frames

Data de registro: 2026-07-21

Objetivo: registrar a etapa em que o Gera Videos/Gera Frames reaproveita frames/imagens base para gerar aproximadamente 18 videos por rodada, alterando frames e mantendo a logica criativa do produto.

Nota operacional: Gera Videos/Gera Frames e Drive sao coisas diferentes. O Gera gera/multiplica os videos; o Drive apenas armazena, organiza e rastreia o resultado exportado.

## Onde entra no fluxo

Fluxo atualizado:

```text
Mercado/EchoTik
> produto aprovado
> Google Flow / CapCut / video-base
> Gera Videos / Gera Frames
> lote de videos gerados
> armazenar resultado exportado no Drive, na pasta do produto
> publicacao
> metricas da conta
> aprendizado
```

## Conceito operacional

Uma rodada de producao nao deve ser tratada como "um video".

Ela deve ser tratada como:

```text
produto_id
> lote_gera_frames
> prompt_padrao / imagem_base / frames
> 18 variacoes aproximadas
> filtro de qualidade
> videos aprovados para CapCut/publicacao
```

## Entidades

### Produto

O produto aprovado pela etapa de mercado.

Campos essenciais:

- produto_id;
- nome;
- decisao;
- comissao;
- risco_compliance;
- status_producao.

### Lote Gera Videos / Gera Frames

Representa uma rodada feita no Gera Videos/Gera Frames.

Campos essenciais:

- lote_id;
- produto_id;
- ferramenta (`Gera Videos` ou `Gera Frames`);
- quantidade_planejada;
- quantidade_gerada;
- quantidade_aprovada;
- quantidade_descartada;
- imagem_base_version;
- prompt_padrao_version;
- frame_set_id;
- ambiente_base;
- status;
- pasta_drive_url, apenas como local de armazenamento do resultado exportado;

### Conteudo individual

Cada video gerado dentro do lote.

Campos essenciais:

- conteudo_id;
- lote_id;
- produto_id;
- variante_num;
- ambiente;
- arquivo_drive_url;
- status;
- check_visual;
- check_audio;
- check_compliance;
- arquivo_final_url;
- publicacao_id;

## Quando atualizar imagem base ou prompt padrao

Atualizar imagem base quando:

- a embalagem aparece ilegivel;
- produto deforma com frequencia;
- ambiente confunde o objeto;
- frame inicial nao prende atencao;
- as variacoes ficam parecidas demais.

Atualizar prompt padrao quando:

- aparecem muitos simbolos/logos inventados;
- a cena gera claims sensiveis;
- o audio vem inconsistente;
- as maos ou objetos deformam;
- a fala/cena termina em momentos ruins;
- o lote gera menos de 30% de videos aproveitaveis.

## Criterio de aproveitamento do lote

Para cada lote de aproximadamente 18 videos:

```text
aproveitamento = quantidade_aprovada / quantidade_gerada
```

Leitura inicial:

| Aproveitamento | Decisao |
|---|---|
| 0% a 20% | Reprovar lote e revisar prompt/imagem base. |
| 21% a 40% | Usar melhores outputs, mas corrigir causa principal. |
| 41% a 70% | Lote aceitavel para operacao inicial. |
| acima de 70% | Prompt/frame set candidato a padrao reutilizavel. |

## Padrao real identificado nos arquivos

Os lotes validos no Drive usam nomes como:

```text
PRODUTO_G1C1A1_DDMM_ID.mp4
PRODUTO_G3C2A2_DDMM_ID.mp4
```

Leitura dos codigos:

- `G`: gancho.
- `C`: corpo.
- `A`: action/CTA.

Exemplo:

```text
ESCORREDOR_G2C3A1_2007_1784562975785.mp4
```

Interpretacao:

```text
produto: ESCORREDOR
gancho: G2
corpo: C3
action/CTA: A1
data/lote: 2007
id_export: 1784562975785
```

Esse padrao deve ser usado para identificar automaticamente videos do Gera Videos/Gera Frames, agrupar variacoes por produto e reconstruir a matriz criativa `gancho x corpo x action`.

Observacao: alguns videos desses lotes podem ja ter sido postados e ainda nao apagados/movidos. Portanto, o inventario do Drive indica "arquivo encontrado", mas a disponibilidade real para publicacao precisa de uma conferencia manual antes de entrar no calendario.

## Pasta por produto no Drive

Quando um produto entrar em producao, criar uma pasta propria:

```text
TikTok Shop Operacao/
  01_produtos_validados/
    produto-{produto_id}_{slug}/
      00_referencias/
      01_frames_base/
      02_lotes_gerados/
      03_capcut/
      04_finais/
      05_postados/
      06_descartados/
```

Regra: salvar videos por produto, nao soltos em uma pasta geral.

## Naming recomendado

Lote:

```text
lote-{produto_id}-{yyyymmdd}-{numero}
```

Video individual:

```text
{produto_slug}_G{gancho}C{corpo}A{action}_{ddmm}_{id_export}.mp4
```

Exemplo:

```text
ESCORREDOR_G2C3A1_2007_1784562975785.mp4
```

## Regra de dashboard

O dashboard deve mostrar dois niveis:

1. Producao por lote:
   - gerados;
   - aprovados;
   - descartados;
   - aproveitamento;
   - causa principal de descarte.

2. Resultado por video publicado:
   - views;
   - cliques;
   - pedidos;
   - GMV;
   - comissao;
   - resultado 24h/72h/7d.

## Aprendizado esperado

Depois de alguns lotes, queremos saber:

- quais prompts padrao geram mais videos aproveitaveis;
- quais imagens base funcionam melhor;
- quais ambientes reduzem deformacoes;
- quais variacoes vendem melhor depois de publicadas;
- se vale atualizar imagem base/prompt antes de gerar novo lote.
