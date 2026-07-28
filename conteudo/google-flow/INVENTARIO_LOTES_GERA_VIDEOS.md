# Inventario FLOW - lotes Gera Videos / Gera Frames

Data de registro: 2026-07-21  
Ultima leitura da planilha-mae: 2026-07-28

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

## Snapshot atual da aba `estoque_videos`

Leitura de 2026-07-28, com dados do snapshot 2026-07-24:

- 21 produtos/pastas inventariados.
- 303 videos disponiveis/inventariados.
- Ritmo-base: 2 videos por produto/dia.
- Horizonte do calendario: 2026-07-22 a 2026-08-04.
- Maior cobertura: `CEBO DE CARNEIRO / CREME MULHER`, com 38 videos e 19 dias.

| Produto/pasta | Perfil sugerido | Videos | Cobertura |
|---|---|---:|---:|
| `ESCOVA ELETRICA` | pendente_conta | 24 | 12 |
| `BS ARABE AMBER ROYALE` | Ceci | 12 | 6 |
| `BS ARABE MASC KIT 3` | Ceci | 12 | 6 |
| `HEMORROIDA / KREAIN NATURE` | Fe | 10 | 5 |
| `HIDRABENE / PROTETOR HIDRABENE` | Ceci | 8 | 4 |
| `HIDRABENE / KIT BS + CREME` | Ceci | 10 | 5 |
| `HIDRABENE / KIT 4 BODY CREAM` | Ceci | 10 | 5 |
| `HIDRABENE / KIT HIDRABENE` | Ceci | 10 | 5 |
| `BODY SPLASH AYRA` | Ceci | 10 | 5 |
| `KIT 2 TRAVESSEIROS` | Ceci | 24 | 12 |
| `ESCORREDOR` | Ceci | 10 | 5 |
| `CEBO DE CARNEIRO / CREME MULHER` | Ceci | 38 | 19 |
| `KIT PANO MICROFIBRA` | Ceci | 7 | 4 |
| `BS ISLY FEM KIT6` | Ceci | 10 | 5 |
| `KIT CALCA INFANTIL` | Ceci | 10 | 5 |
| `PRO 3 MAGNESIO` | Fe | 28 | 14 |
| `MAQUININHA` | Fe | 28 | 14 |
| `BODY SPLASH FLORAT RED EGEU` | Ceci | 6 | 3 |
| `BS ARABE AYRA + AMBER KIT` | pendente_perfil | 14 | 7 |
| `BODY SPLASH AGAPIS` | pendente_perfil | 6 | 3 |
| `BS BARBOUR'S MASC KIT2` | Ceci | 16 | 8 |

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
