# Troca da origem Google Drive - 2026-08-08

Leitura realizada em `2026-08-08T19:35:47-03:00`, em modo somente leitura.

## Nova origem

- Conta esperada: `ceciliafigueiredo.shop@gmail.com`
- Pasta raiz ativa: `FLOW`
- ID: `1XA8zgZNAMjhRvonypu1hb7cm3b9iX4Gc`
- URL: `https://drive.google.com/drive/folders/1XA8zgZNAMjhRvonypu1hb7cm3b9iX4Gc`
- Sessao do navegador: acesso confirmado na conta esperada.
- Conector Google Drive/API: ainda autenticado em `isabelarosar@gmail.com`; precisa ser reconectado antes de reativar a rotina das 07:00.

## Estrutura observada

| Pasta | ID |
|---|---|
| CANTINHO DA REFLEXAO | `1g_QRNvkSKd8bhk2nZRccugW32kR1M18J` |
| CECI FIGUEIREDO - PERFIL 1 | `1ZW60VcZumB1zIZ4Kd5X_AJxnt293Xps6` |
| FE FIGUEIREDO - PERFIL 2 | `19bb4YLntPmm5531c7IVnb8whi2GLGfOD` |
| VISTAS LINDAS | `1KswszXZFEbZ8Wt-_5XtCQM1SXOL39cOS` |

No perfil `CECI FIGUEIREDO - PERFIL 1` foram observadas as categorias `CASA E UTILIDADES`, `CASE ITACHI`, `FEMININO`, `MASCULINO`, `MEIA BICHINHO` e `SAUDE`.

## Estado operacional

- A origem anterior foi mantida apenas como historico em `dados/drive_operacao_tiktok_shop.json`.
- O banco SQLite anterior foi arquivado em `data/backups/reset-20260808-192707/agent.db`.
- O banco operacional novo foi criado vazio, sem produtos, metricas, conteudos ou filas de teste.
- A automacao diaria esta pausada ate a reconexao do conector na conta esperada.
- Nenhum arquivo do Drive foi apagado, movido, renomeado, compartilhado ou editado.

## Regra de estoque

Na proxima leitura completa, contar apenas arquivos de video cujo nome contenha `G<number>C<number>A<number>` ou `FINAL`, sem diferenciar maiusculas e minusculas, com deduplicacao por ID do Drive.

Este registro documenta somente a troca de fonte. As quantidades por produto permanecem pendentes ate a reconexao da API, para evitar uma contagem parcial pela interface do navegador.
