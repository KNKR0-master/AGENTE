# Inventario FLOW - lotes Gera Videos / Gera Frames

Data de registro: 2026-07-21  
Ultima leitura da planilha-mae: 2026-08-02

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

## Recontagem estrita de 2026-07-30

## Recontagem estrita de 2026-08-01

## Recontagem estrita de 2026-08-02

Leitura somente consulta das 21 pastas de `estoque_videos`, incluindo subpastas: 402 videos validos. A contagem aceita somente arquivos de video cujo nome tenha `G<number>C<number>A<number>` ou `FINAL`, deduplicados por ID do Drive. Nenhuma pasta ficou pendente de verificacao; 10 produtos ficaram abaixo de 8 videos.

Leitura somente consulta das 21 pastas de `estoque_videos`, incluindo subpastas: 402 videos validos. A contagem aceita somente arquivos de video cujo nome tenha `G<number>C<number>A<number>` ou `FINAL`, deduplicados por ID do Drive. Nenhuma pasta ficou pendente de verificacao; 10 produtos ficaram abaixo de 8 videos.

## Recontagem estrita de 2026-07-31

Leitura somente consulta das 21 pastas de `estoque_videos`, incluindo subpastas: 426 videos validos. A contagem aceita somente arquivos de video cujo nome tenha `G<number>C<number>A<number>` ou `FINAL`, deduplicados por ID do Drive. Nenhuma pasta ficou pendente de verificacao; 9 produtos ficaram abaixo de 8 videos.

Leitura somente consulta das 21 pastas de `estoque_videos`: 193 vídeos válidos. A recontagem usa nome e ID do arquivo, incluindo subpastas aplicáveis, e conta somente vídeos com `G<number>C<number>A<number>` ou `FINAL`; os demais `.mp4` foram excluídos. Nenhuma pasta ficou pendente de verificação.

## Recontagem estrita de 2026-07-29

Leitura somente consulta das 21 pastas de `estoque_videos`: 188 videos validos. A recontagem usa o nome do arquivo e ID do Drive, contando somente videos com `G<number>C<number>A<number>` ou `FINAL`; os demais `.mp4` foram excluidos. Nenhuma pasta ficou pendente de verificacao.

Alguns videos podem ja ter sido postados e ainda permanecerem no Drive. Por isso:

1. `arquivo encontrado` nao significa automaticamente `pronto_para_postar`.
2. Antes de programar publicacao, o usuario confere e remove/marca manualmente videos ja usados.
3. A automacao deve registrar o lote como `inventariado` ou `pendente_limpeza_manual` ate essa conferencia.
4. So depois da limpeza/revisao o status deve virar `pronto_para_postar`.

## Regra revisada de contagem

Para evitar inflar estoque com arquivos que nao sao variacoes reais de publicacao, a contagem valida deve considerar somente:

1. Arquivos de video cujo nome contenha o padrao `G<number>C<number>A<number>`, por exemplo `G1C2A3` ou `G12C4A8`.
2. Arquivos de video cujo nome contenha a palavra `FINAL`, sem diferenciar maiusculas/minusculas.

Nao contam como estoque:

- `.mp4` sem padrao `G<number>C<number>A<number>` e sem `FINAL`;
- previews, rascunhos ou arquivos antigos;
- imagens, prompts, textos, thumbnails ou arquivos auxiliares;
- duplicatas que nao representem uma variacao publicavel.

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
- identifica arquivos `.mp4` com padrao `G<number>C<number>A<number>` ou palavra `FINAL`;
- agrupa por produto, data/lote e id export;
- calcula quantidade por lote;
- marca lote completo quando houver 18 variacoes;
- marca lote parcial quando houver menos de 18;
- nao move nem apaga nada;
- gera uma tabela para revisao humana antes de popular calendario/publicacoes.
