# TikTok Studio browser analytics

## Objetivo

Coletar diariamente, com uma sessao autenticada pelo proprio usuario, o catalogo
de videos e as metricas oficiais exibidas pelo TikTok Studio. A coleta usa um
perfil Playwright persistente local, nao armazena senha e nao tenta contornar
login, CAPTCHA ou MFA.

O coletor alimenta snapshots imutaveis no SQLite. Valores ausentes continuam
`NULL`; zero so e persistido quando a interface mostra zero explicitamente.

## Rotas validadas em 2026-08-17

- Catalogo completo: `https://www.tiktok.com/tiktokstudio/content`.
- Analytics da conta: `https://www.tiktok.com/tiktokstudio/analytics`.
- Top posts: `https://www.tiktok.com/tiktokstudio/analytics/content`.
- Video, visao geral: `/tiktokstudio/analytics/{video_id}/overview`.
- Video, espectadores: `/tiktokstudio/analytics/{video_id}/viewers`.
- Video, engajamento: `/tiktokstudio/analytics/{video_id}/engagement`.
- Monetizacao: `https://www.tiktok.com/tiktokstudio/monetization`.

O catalogo observado tinha 1.307 publicacoes e usa uma lista virtualizada. A
pagina `analytics/content` mostrou somente top posts e nao deve ser usada como
fonte do catalogo completo.

O seletor `[data-tt="components_PostTable_Absolute"]` continuava presente na
interface autenticada em 17/08. A falha original ocorreu porque o perfil
Playwright do agendador estava desautenticado; o coletor antigo interpretava a
ausencia de linhas como `UI_CHANGED` e ainda perdia o screenshot ao fechar o
Playwright antes do tratamento do erro.

## Readiness e navegacao segura

O modo padrao e `SAFE`. Toda pagina segue obrigatoriamente:

`NAVEGAR -> ESPERAR DOM -> ESPERAR DADOS -> ESTABILIZAR -> EXTRAIR -> VALIDAR -> AVANCAR`.

A maquina de estados registra `NAVIGATING`, `DOM_READY`, `WAITING_FOR_DATA`,
`DATA_DETECTED`, `WAITING_FOR_STABILITY`, `READY`, `EXTRACTING`, `VALIDATING`
e `DONE`. Navegacao, troca de aba e rolagem sao bloqueadas fora dos estados
permitidos.

Uma tela so chega a `READY` quando, ao mesmo tempo:

1. o `video_id` da URL e da pagina corresponde ao item esperado;
2. nao ha loader ou skeleton visivel;
3. nao ha request XHR/fetch relevante pendente;
4. os campos obrigatorios contem valores reais, inclusive zero explicito;
5. a mesma assinatura foi lida tres vezes;
6. o DOM relevante permaneceu quieto por pelo menos 1.800 ms.

Placeholders como `--`, `Loading` e campo vazio nunca sao convertidos em zero.
Quando `views=0` aparece antes da resposta de analytics, o coletor aplica uma
guarda inicial de 4 segundos. Um zero confirmado pela resposta da rede continua
sendo um valor valido. A rede lenta simulada mostrou `--`, depois `0` e depois
`18`; o valor aceito foi `18` apos 4,39 s e tres leituras estaveis.

O timeout padrao e 30 s para navegacao. Analytics usa uma escada de 15, 25 e
40 s nas tres tentativas, limitada pelo teto configuravel de 45 s. Uma falha
na pagina obrigatoria nao e
persistida como sucesso; paginas opcionais podem terminar em `DATA_UNAVAILABLE`
somente quando a propria interface apresenta um estado terminal explicito.

O TikTok pode passar temporariamente por `/login?...redirect_url=...` mesmo com
a sessao ativa e mostrar "voce esta conectado, nao e preciso repetir". Esse
handoff agora e aguardado; o coletor segue apenas o `redirect_url` oficial do
dominio TikTok e so declara `AUTH_REQUIRED` se continuar na tela de login.

Cada snapshot guarda `extraction_status`, `data_completeness_score`,
`load_duration_ms`, `retry_count` e o historico de readiness. Cada run resume
completos, parciais, timeouts, tentativas, tempo medio, P95 e completude media.
O gate reprova o lote antes da persistencia se completos ficarem abaixo de 70%,
completude obrigatoria media abaixo de 60% ou timeouts acima de 30%.

## Descoberta e cobertura

O catalogo combina, sem depender de uma unica estrategia:

1. respostas JSON observadas durante a navegacao normal;
2. links com `/video/{id}`;
3. atributos semanticos `data-video-id`, `data-item-id`, `data-content-id` e `data-aweme-id`;
4. cards/tabela mapeados;
5. JSON incorporado e rolagem da lista virtualizada.

O `video_id` e sempre a chave. Legenda, posicao e thumbnail nunca sao usados
como identificador. O loop termina quando alcanca a janela pedida ou passa por
ciclos consecutivos sem IDs novos.

Cada run registra quantidade esperada, descoberta e coletada, cobertura,
confianca da estimativa, metodo, novos IDs, falhas, duracao e validade. Runs com
menos de 70% nao criam snapshots analiticos; de 70% a 90% ficam parciais e so
permitem `OBSERVAR`; a partir de 90% liberam a analise completa.

Para manter a rotina diaria viavel em contas de alto volume, todos os videos
descobertos recebem snapshot das metricas estruturadas do catalogo e os 60
videos prioritarios (configuravel por `TIKTOK_STUDIO_MAX_DETAIL_VIDEOS`) recebem
tambem as telas de Overview/Viewers. Snapshots apenas de catalogo ficam
`PARTIAL` e nunca podem gerar `ESCALAR` ou `TESTAR`.

## Campos coletados

Catalogo:

- TikTok Video ID extraido da URL;
- URL, legenda, thumbnail, duracao e data de publicacao;
- status/privacidade;
- views, likes e comentarios exibidos na linha.

Detalhe `overview`:

- views;
- tempo total e medio de visualizacao;
- percentual que assistiu ao video completo;
- novos seguidores;
- origem Para voce, Pesquisa, Perfil pessoal e Seguindo.

Detalhe `viewers`:

- alcance/total de espectadores;
- percentual de espectadores novos e recorrentes, quando a amostra permite.

O arquivo de seletores e conversoes fica em
`config/tiktok_studio_mapping.json`. Ele e versionado separadamente da logica
para permitir remapeamento quando a interface mudar.

## Campos nao encontrados no Studio web

Na conta e telas observadas nao havia, por video, campos oficiais de:

- impressoes do produto, cliques no produto, CTR ou add-to-cart;
- pedidos, GMV, comissao e Shop Ads;
- impressoes gerais separadas de views.

Esses valores nao sao inferidos. GMV, pedidos e comissao continuam vindo do
Affiliate Orders XLSX, por correspondencia direta do Content ID. O relatorio
atual termina em 2026-08-08; por isso velocidades comerciais de 24h ficam
`NULL`, e nao zero, ate a chegada de um relatorio recente.

Na pagina de monetizacao foi confirmado o programa TikTok Shop para Criadores,
mas o botao `Ver` abre somente um dialogo com QR para o aplicativo TikTok. Nao
ha uma tela web adicional validada para extrair analytics do Shop Creator.

## Sessao inicial

Instale o navegador uma vez:

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

Depois abra o perfil local e conclua o login manualmente:

```powershell
python collect_tiktok_analytics.py --setup-session
```

Quando o TikTok Studio estiver autenticado, volte ao terminal e pressione
`ENTER`. O perfil fica em `data/tiktok-browser-profile`, ignorado pelo Git.
O canal padrao e o Google Chrome instalado (`TIKTOK_STUDIO_BROWSER_CHANNEL=chrome`)
para manter o fluxo de login manual compativel com a conta Google. O coletor
abre a rota oficial `https://www.tiktok.com/login?fpm=true`, que oferece o
provedor Google, e nao interage com o formulario de login.

## Coleta e recomputacao

```powershell
python collect_tiktok_analytics.py
python collect_tiktok_analytics.py --force
python collect_tiktok_analytics.py --last-n-days 30
python collect_tiktok_analytics.py --full-backfill
python collect_tiktok_analytics.py --remap --last-n-days 30
python collect_tiktok_analytics.py --validate-video-id 7674453719018245384 --validate-video-id 7674453513665023250
python collect_tiktok_analytics.py --recompute-only
```

Sem `--force`, uma segunda execucao concluida no mesmo dia e ignorada de forma
idempotente. Cada tentativa registra status, pagina, erro e contagens em
`tiktok_analytics_runs`. Falhas guardam screenshot e JSON diagnostico em
`logs/tiktok-collector-errors`. O modo `--remap` observa UI e rede e gera
`logs/tiktok-network-discovery-*.json` sem modificar o banco.

O relatorio de rede guarda URL com valores de query removidos, metodo, status,
content-type, tamanho, campos JSON de primeiro nivel e quantidade de IDs. Nao
guarda cookies, tokens nem headers de autorizacao.

Estados possiveis:

- `SUCCESS` ou `PARTIAL_SUCCESS`;
- `AUTH_REQUIRED`;
- `INTERACTION_REQUIRED`;
- `UI_CHANGED`;
- `COVERAGE_CRITICAL`;
- `FAILED_VALIDATION`;
- `RATE_LIMITED`;
- `FAILED`.

## Agendamento

O Agendador de Tarefas chama `run_tiktok_analytics_daily.ps1` todos os dias as
07:00. Logs ficam em `data/logs/tiktok-analytics-AAAA-MM-DD.log`.

O setup e as coletas usam o modo configurado em `TIKTOK_STUDIO_HEADLESS`; o
padrao atual e browser visivel, pois precisa manter o mesmo comportamento
validado manualmente. A tarefa requer uma sessao
interativa do Windows e um login TikTok ainda valido. Se houver CAPTCHA, MFA ou expiracao,
o run termina como `INTERACTION_REQUIRED` ou `AUTH_REQUIRED` e aguarda acao
manual; nao ha tentativa de bypass.

## Remapeamento quando a UI mudar

1. Execute `python collect_tiktok_analytics.py --remap --last-n-days 30`.
2. Revise o screenshot, as estrategias e os endpoints sanitizados no relatorio.
3. Atualize apenas `config/tiktok_studio_mapping.json` quando possivel.
4. Rode os testes e valide cinco videos antes da coleta completa.
5. Confirme que ausencias continuam `NULL` e que nenhum total caiu de forma
   impossivel antes de liberar a coleta diaria.
