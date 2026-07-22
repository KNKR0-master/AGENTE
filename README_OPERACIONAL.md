# Fluxo operacional do agente TikTok Shop

## Objetivo

Transformar dados crus do Kalodata em uma base local de decisão:

- produto monitorado;
- snapshots de mercado;
- top vídeos;
- top criadores;
- status de validação;
- fila de produção com no mínimo duas versões por produto validado.

## Como aproveitamos a branch `main`

A branch `main` adicionou a camada operacional que complementa este MVP local:

- `VISAO_GERAL_PROJETO.md`: visão integrada dos pilares do projeto.
- `OPERACAO_TIKTOK_SHOP.md`: mapa da planilha mãe, contas, produtos, conteúdo, calendário e métricas.
- `FINANCEIRO_RESULTADOS_TIKTOK.md`: conciliação de GMV, comissão estimada, comissão concluída, saldos e saques.
- `GOOGLE_FLOW.md` e `VALIDACAO_GOOGLE_FLOW.md`: regras para transformar produto validado em criativos.
- `conteudo/google-flow/`: inventário, multiplicação de frames e revisão econômica de vídeos.
- `dados/drive_operacao_tiktok_shop.json`: IDs e URLs da estrutura no Drive/Sheets.

Este dashboard fica como a camada local de leitura, decisão e acompanhamento. A planilha/Drive ficam como camada operacional externa até autorizarmos automações de escrita.

## Banco local

O banco SQLite fica em `data/agent.db`.

Ele guarda:

- `products`: cadastro e decisão do produto;
- `product_snapshots`: totais por janela de análise;
- `video_metrics`: linhas da exportação de vídeos;
- `creator_metrics`: linhas da exportação de criadores;
- `production_tasks`: versões de vídeo para produzir, postar, programar e medir.

O arquivo `.db` é local e não entra no Git.

## Importar exportações do Kalodata

Exemplo:

```powershell
python ingest_kalodata.py `
  --video-xlsx "C:\Users\Usuario\Downloads\Kalodata_Video_20260714071513_BR.xlsx" `
  --creator-xlsx "C:\Users\Usuario\Downloads\Kalodata_Creator_20260714071447_BR.xlsx" `
  --price 64 `
  --commission-rate 15 `
  --shop-name "Manzanas estofadas" `
  --validated
```

Use `--validated` quando o produto já deve entrar na fila de produção. Isso cria automaticamente as versões 1 e 2.

## Abrir dashboard local

```powershell
python dashboard_server.py
```

Depois abra:

```text
http://127.0.0.1:8765
```

## Automação Kalodata via navegador

Próxima camada planejada:

1. Receber nome ou link do produto.
2. Abrir Kalodata no navegador autenticado via Di-cloak.
3. Buscar o produto.
4. Selecionar janela de análise.
5. Exportar ou extrair dados de produto, vídeos e criadores.
6. Importar no banco local.
7. Atualizar o dashboard.

Por envolver sessão logada, cookies e navegação em conta real, essa automação só deve ser ativada com aprovação explícita antes do primeiro uso.

## Regra de produção

Quando um produto for marcado como `validated`, o sistema cria duas tarefas:

- `Versão 1`: postar na hora;
- `Versão 2`: deixar programada.

Cada versão pode registrar:

- status;
- gancho;
- formato;
- data postada;
- data programada;
- observações;
- métricas posteriores.
