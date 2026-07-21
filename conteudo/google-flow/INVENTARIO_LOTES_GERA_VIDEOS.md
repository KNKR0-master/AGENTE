# Inventario FLOW - lotes Gera Videos / Gera Frames

Data de registro: 2026-07-21

Fonte: leitura somente consulta da pasta Drive `FLOW`.

Regra de seguranca: nao apagar, mover, renomear ou editar arquivos sem autorizacao explicita.

## Padrao confirmado

Os lotes validos do Gera Videos/Gera Frames usam o padrao:

```text
PRODUTO_GxCyAz_DDMM_ID.mp4
```

Onde:

- `G` = gancho.
- `C` = corpo.
- `A` = action/CTA.

Esse padrao foi confirmado pelo usuario e deve ser usado como detector primario de lote Gera.

## Lotes completos validados

| Produto/pasta | Status | Qtde observada | Observacao |
|---|---:|---:|---|
| `HEMORROIDA` | lote Gera completo | 18 | Validado pelo usuario. |
| `HIDRABENE / PROTETOR_HIDRABENE` | lote Gera completo | 18 | Validado pelo usuario. |
| `HIDRABENE / KIT BS + CREME HIDRABENE` | lote Gera completo | 18 | Validado pelo usuario. |
| `HIDRABENE / KIT 4 BODY CREAM` | lote Gera completo | 18 | Validado pelo usuario. |
| `HIDRABENE / KIT HIDRABENE` | lote Gera completo | 18 | Validado pelo usuario. |
| `BODY SPLASH AYRA` | lote Gera completo | 18 | Validado pelo usuario. |
| `travesseiro / kit 2 travesseiros` | lote Gera completo | 18 | Tambem ha `FINAL 2.mp4`. |
| `ESCORREDOR` | lote Gera completo | 18 | Validado pelo usuario. |
| `CREME / mulher` | lote Gera completo | 18 | Tambem ha 3 finais na raiz. |

## Lotes parciais/candidatos validados

| Produto/pasta | Status | Qtde observada | Observacao |
|---|---:|---:|---|
| `CHINELO SLIDE` | lote Gera parcial | 14 | Validado pelo usuario como candidato. |
| `BS ISLY FEM-KIT6 / GERAVIRAL` | lote Gera parcial | 16 | Validado pelo usuario como candidato. |
| `PANO MICROFIBRA` | lote Gera parcial | 13 | Validado pelo usuario como candidato. |
| `kit calca / geraviral / VIDEOS` | lote Gera parcial | 16 | Validado pelo usuario como candidato. |

## Regra de disponibilidade

Alguns videos podem ja ter sido postados e ainda permanecerem no Drive. Por isso:

1. `arquivo encontrado` nao significa automaticamente `pronto_para_postar`.
2. Antes de programar publicacao, o usuario confere e remove/marca manualmente videos ja usados.
3. A automacao deve registrar o lote como `inventariado` ou `pendente_limpeza_manual` ate essa conferencia.
4. So depois da limpeza/revisao o status deve virar `pronto_para_postar`.

## Classificacao recomendada

| Status operacional | Uso |
|---|---|
| `inventariado` | Arquivo encontrado no Drive, ainda sem conferencia manual. |
| `pendente_limpeza_manual` | Lote pode conter videos ja postados. |
| `pendente_revisao` | Precisa revisar visual/audio/compliance. |
| `pronto_para_postar` | Aprovado para entrar no calendario. |
| `postado` | Ja publicado e registrado. |
| `descartado` | Nao deve ser usado. |

## Proxima automacao

Criar uma rotina de inventario que:

- percorre a pasta `FLOW`;
- identifica arquivos `.mp4` com padrao `GxCyAz`;
- agrupa por produto, data/lote e id export;
- calcula quantidade por lote;
- marca lote completo quando houver 18 variacoes;
- marca lote parcial quando houver menos de 18;
- nao move nem apaga nada;
- gera uma tabela para revisao humana antes de popular calendario/publicacoes.
