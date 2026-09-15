# Resultado da tentativa unica - 10/09/2026

Run: `5db008ee-24a3-4af7-8425-96de882f4735`
Estado terminal persistido: **UNKNOWN**.
Uma autorizacao, um consumo, uma execucao. session_verified=0; session_verified_at=NULL.

## Execucao

O executor local PID 4260 usou PublisherWorker e a mesma base SQLite; nao houve
endpoint de disparo no servidor PID 19856. O servidor nao tinha contexto ativo.
Chrome principal PID 18880, 1 contexto, 1 pagina (`page-1`).
Profile: `data/publisher/browser_profiles/a9a3acebc275495a8271b74e73faeab6`.
channel=chrome, headless=False, viewport=None, chromium_sandbox=True.
Processo observado sem --no-sandbox. Listeners instalados pelo worker antes do goto.
Nenhum upload, post, agendamento, reload ou retry executado pelo agente.
Uma unica request get_qrcode foi capturada durante o run.
Ao terminar, a coleta do experimento foi encerrada. O Chrome foi preservado aberto;
scripts proprios da pagina nao foram modificados ou interrompidos.

## Timeline resumida (America/Sao_Paulo, UTC-3)

| Hora | Evidencia |
|---|---|
| 14:25:38 | Autorizacao criada e consumida no SQLite |
| 14:25:40 | Trace iniciado |
| 14:25:41 | Navegacao principal /login; HTTP 200 |
| 14:25:42 | LOGIN_PAGE_OPENED; documento interactive; estado semantico UNKNOWN |
| 14:25:52 | Mesma pagina navegou para /login/qrcode |
| 14:25:53 | GET /passport/web/get_qrcode respondeu 200, uma unica vez |
| 14:25:54 | Primeiras respostas check_qrconnect HTTP 200 |
| 14:26:50 | Usuario informou QR visivel e confirmacao no celular; marcador USER |
| 14:26:50-14:27:49 | 100 respostas HTTP 200 de check_qrconnect apos marcador |
| 14:27:50 | Limite de 60 segundos; FINAL_STATE e UNKNOWN persistido |

O instante exato em que a imagem ficou visivel nao foi detectado automaticamente.
14:25:53 e resposta da geracao, nao prova de renderizacao. A confirmacao humana foi
registrada as 14:26:50, nao necessariamente no instante exato do scan.
Nao houve transicao semantica detectada para QR_SCANNED ou CONFIRMATION_COMPLETE.
QR_STATE_CHANGED inicial indica inicializacao para UNKNOWN, nao scan comprovado.

## HTTP, console e navegacao

174 respostas check_qrconnect HTTP 200: 58 em cada host login-us.www.tiktok.com,
login-eu.www.tiktok.com e web-sg.tiktok.com. Polling permaneceu ativo.
Nenhum HTTP 401/403/408/409/429/5xx, requestfailed ou pageerror foi registrado.
Console: 223 eventos error, 5 warning, 5 log, 5 debug e 4 info.
Mensagens/argumentos foram omitidos pela sanitizacao preparada; nao e possivel
atribuir causa ou afirmar qual erro JavaScript ocorreu a partir desses niveis.
Nenhum popup ou segunda pagina registrado. Navegacoes principais observadas:
/login -> /login/qrcode. Nenhuma navegacao autenticada apos confirmacao informada.
Nao houve redirect HTTP 3xx capturado; nao inferir 302 pela mudanca de URL.

## Cookies e storage

7 cookies TikTok no primeiro snapshot; 8 no ultimo. Adicionado s_v_web_id.
Expiracoes de cookies existentes foram atualizadas, incluindo odin_tt e msToken.
Nenhum sessionid, sessionid_ss ou sid_tt valido detectado durante o run.
Valores de cookies nunca foram registrados.
LocalStorage manteve a mesma lista sanitizada (25 entradas; nomes longos redigidos).
SessionStorage passou de zero para 11 chaves, incluindo webapp_launch_mode,
webapp_session_id, webapp-session-referer, webapp_extra_data, msToken e
webapp_tiktok_open. Nomes de chaves nao comprovam sessao autenticada.
IndexedDB: cdn-failover, secure-store e sw_storage, todos versao 1, sem mudanca.
Um service worker activated, mesmo escopo. Valores de storage nao coletados.
Mudancas/reordenacoes de metadados podem gerar snapshots; nao equivalem a login.

## Diagnostico e limites

Primeiro ponto comprovado: apos a confirmacao informada, o desktop continuou no QR,
com polling 200, sem cookie de sessao reconhecido ou entrada na area autenticada.
Nao e possivel localizar a falha dentro da resposta de polling, porque corpos HTTP
nao foram coletados. HTTP 200 nao confirma sucesso da operacao de autenticacao.
Hipotese compativel: a confirmacao nao se converteu em sessao desktop utilizavel,
ou a pagina nao processou o resultado. Nao ha evidencia para escolher entre essas
possibilidades, culpar sandbox, afirmar bloqueio TikTok ou rejeicao do login.
Tambem existe uma lacuna comprovada do detector: a UI QR visivel ao usuario nao
produziu sinais semanticos reconhecidos. Por isso UNKNOWN e a classificacao correta.
Nenhuma correcao foi executada depois do diagnostico.

## Evidencia completa

Timeline integral: 1642 registros JSONL com timestamps UTC (+00:00), incluindo
todos os eventos HTTP e snapshots sanitizados:
`data/publisher/evidence/login-1-2026-09-10T17-25-40+00-00.jsonl`.
