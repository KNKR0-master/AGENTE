# Gera Frames - multiplicacao de videos por frames

Data de registro: 2026-07-21

Objetivo: registrar a etapa em que o Gera Frames reaproveita frames/imagens base para gerar aproximadamente 18 videos por rodada, alterando frames e mantendo a logica criativa do produto.

Nota operacional: Gera Frames e Drive sao coisas diferentes. O Gera Frames gera/multiplica os videos; o Drive apenas armazena, organiza e rastreia o resultado exportado.

## Onde entra no fluxo

Fluxo atualizado:

```text
Mercado/EchoTik
> produto aprovado
> Google Flow / CapCut / video-base
> Gera Frames
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

### Lote Gera Frames

Representa uma rodada feita no Gera Frames.

Campos essenciais:

- lote_id;
- produto_id;
- ferramenta (`Gera Frames`);
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
produto-{produto_id}_{slug}_lote-{numero}_var-{variante}_amb-{ambiente}_{status}.mp4
```

Exemplo:

```text
produto-1732849645859341976_tetra-magnesio_lote-001_var-07_amb-cozinha_final.mp4
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
