# TikTok Studio - referencia manual de 5 videos

Captura manual realizada em 17/08/2026, aproximadamente 07:53 BRT, na tela
autenticada `TikTok Studio > Analytics > Visao geral`. Estes valores nao foram
gravados como snapshots de producao.

| video_id | views | likes | comments | shares | saves | watch total | watch medio | completo | seguidores | GMV no Studio |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| 7674453719018245384 | 67 | 0 | 0 | 0 | 0 | 0h:04m:06s | 5.47s | 2.2% | 0 | indisponivel |
| 7674453513665023250 | 66 | 1 | 0 | 0 | 0 | 0h:04m:50s | 4.68s | 1.6% | 0 | indisponivel |
| 7674453063133875474 | 45 | 0 | 0 | 0 | 0 | 0h:03m:08s | 4.97s | 5.3% | 0 | indisponivel |
| 7674452763702480146 | 172 | 0 | 0 | 0 | 0 | 0h:06m:37s | 2.84s | 2.9% | 0 | indisponivel |
| 7674452460546690322 | 732 | 7 | 0 | 1 | 1 | 0h:20m:13s | 1.9s | 0.9% | 1 | indisponivel |

O GMV video a video nao aparece nessa tela do Studio. A fonte comercial segue
sendo o relatorio `Affiliate Orders`, conciliado pelo Content ID.

O lado `Sistema` deve ser gerado, sem gravar no banco, por:

```powershell
python collect_tiktok_analytics.py `
  --validate-video-id 7674453719018245384 `
  --validate-video-id 7674453513665023250 `
  --validate-video-id 7674453063133875474 `
  --validate-video-id 7674452763702480146 `
  --validate-video-id 7674452460546690322
```

## Resultado automatizado

Coleta independente concluida em 17/08/2026, aproximadamente 08:21 BRT, sem
gravar snapshots de producao.

| video_id | views | likes | comments | shares | saves | watch total | watch medio | completo | seguidores |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 7674453719018245384 | 67 | 0 | 0 | 0 | 0 | 246s | 5.47s | 2.2% | 0 |
| 7674453513665023250 | 66 | 1 | 0 | 0 | 0 | 290s | 4.68s | 1.6% | 0 |
| 7674453063133875474 | 45 | 0 | 0 | 0 | 0 | 188s | 4.97s | 5.3% | 0 |
| 7674452763702480146 | 172 | 0 | 0 | 0 | 0 | 397s | 2.84s | 2.9% | 0 |
| 7674452460546690322 | 749 | 7 | 0 | 1 | 1 | 1291s | 1.96s | 1.1% | 1 |

Os quatro primeiros videos coincidiram com a referencia manual. O quinto
recebeu novas visualizacoes entre as capturas, com os contadores sociais
preservados. Relatorio bruto sanitizado:
`logs/tiktok-five-video-validation-2026-08-17_082142.json`.

Status atual: validacao aprovada para a coleta de producao.

## Validacao ampliada de 10 videos

Nova rodada independente em 17/08/2026 as 08:47 BRT, cobrindo videos recentes,
antigos, de alta e baixa audiencia e os tres maiores Content IDs comerciais do
Affiliate Orders. Nenhum snapshot de producao foi criado nesta rodada.

| video_id | views | likes | comments | shares | saves | reach | status | completude | carga | retries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 7674453719018245384 | 68 | 0 | 0 | 0 | 0 | indisponivel | COMPLETE | 100% | 19,97s | 2 |
| 7674453513665023250 | 66 | 1 | 0 | 0 | 0 | indisponivel | COMPLETE | 100% | 12,45s | 0 |
| 7674453063133875474 | 45 | 0 | 0 | 0 | 0 | indisponivel | COMPLETE | 100% | 12,19s | 0 |
| 7674452763702480146 | 172 | 0 | 0 | 0 | 0 | indisponivel | COMPLETE | 100% | 11,48s | 0 |
| 7674452460546690322 | 764 | 7 | 0 | 1 | 1 | indisponivel | COMPLETE | 100% | 13,59s | 0 |
| 7666241176793566482 | 259.000 | 1.422 | 20 | 191 | 165 | 238.000 | COMPLETE | 100% | 11,23s | 0 |
| 7668101914189401362 | 714.000 | 5.919 | 69 | 651 | 691 | 517.000 | COMPLETE | 100% | 12,75s | 0 |
| 7659880710307597576 | 1.100.000 | 11.000 | 115 | 778 | 1.441 | 1.100.000 | COMPLETE | 100% | 14,73s | 0 |
| 7673608408108616967 | 22 | 0 | 0 | 0 | 0 | 21 | COMPLETE | 100% | 12,30s | 0 |
| 7674029008643771656 | 55 | 1 | 0 | 0 | 0 | 51 | COMPLETE | 100% | 13,30s | 0 |

Resumo: 10 completos, 0 parciais, 0 timeouts finais, 2 retries, carga media de
13,40 s, P95 de 19,97 s e completude obrigatoria media de 100%. Relatorio:
`logs/tiktok-five-video-validation-2026-08-17_084741.json`.

## Coleta completa de producao

Run 6 concluido em 17/08/2026 as 09:24 BRT:

- 60 videos descobertos e 60 coletados;
- cobertura de 100%;
- 60 snapshots `COMPLETE`, 0 parciais e 0 falhas;
- 6 retries recuperados e 0 timeouts finais;
- carga media de 12,87 s por video e P95 de 15,29 s;
- completude obrigatoria media de 100%;
- duracao total de 17 min 57 s.

Durante a run, o TikTok abriu uma ponte temporaria de login. A pagina informou
que a sessao continuava conectada, o crawler aguardou o redirecionamento oficial
e retomou o mesmo `video_id`. Nenhum dado da run interrompida anterior havia
sido persistido.
