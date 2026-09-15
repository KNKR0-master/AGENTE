# Ativacao controlada e verificacao do perfil existente

Resultado final: NEEDS_LOGIN.
Data: 08/09/2026. Horarios abaixo em America/Sao_Paulo.

## Encerramento e preservacao

Chrome antigo PID 16440: CloseMainWindow aceito; WaitForExit confirmou saida.
Servidor antigo PID 19176: Ctrl+C enviado ao console apos confirmar que continha
somente o servidor, seu driver 6532 e o helper. O log registrou KeyboardInterrupt.
Os tres processos antigos encerraram. Nao foi usado TerminateProcess/Stop-Process.

Conta #1 CECILIA FIGUEIREDO preservada, sem recriacao.
Mesmo userDataDir:
C:/Users/Usuario/Documents/New project 10/data/publisher/browser_profiles/a9a3acebc275495a8271b74e73faeab6

Nenhuma limpeza de cookies, Local Storage, IndexedDB ou perfil. Escritas normais
do proprio Chrome durante fechamento e navegacao nao foram impedidas. Por isso
preservacao do perfil nao significa igualdade byte a byte depois de usa-lo.
Os seis cookies persistidos foram observados novamente no contexto novo antes
de qualquer navegacao. Nao havia sessionid/sessionid_ss/sid_tt.

## Nova instancia

- Servidor: PID 19668.
- Driver Playwright: PID 17748.
- Chrome principal: PID 25228.
- Um contexto gerenciado, uma pagina, auth_page_id=page-1.
- chromium_sandbox=True, channel=chrome, headless=False, viewport=None.
- Linha de comando real do Chrome conferida: mesmo userDataDir e ausencia de
  --no-sandbox. Nao foi usado ignore_default_args=True.

## Caminho de inspecao sem login

O comando existente /login nao foi utilizado. Foi acrescentado um comando
restrito /api/publisher/inspect-existing-session para esta operacao controlada.
Ele exige perfil ja existente, reutiliza a pagina e permite no maximo uma
navegacao ao Studio por verificacao. Nao cria outro profile.

13:52:14: comando com navigate=false; abriu somente about:blank.
Snapshot inicial: readyState=complete, visibilityState=visible, 1 contexto/1 pagina,
seis cookies TikTok, session_cookie_present=false. Erros de storage em about:blank
foram explicitados, nao interpretados como falha de autenticacao.

13:53:09: unico comando com navigate=true, destino:
https://www.tiktok.com/tiktokstudio

Foi instalado um guard de rede EXPLICITO nesta verificacao para abortar entradas
em login/QR, sem permitir que o destino gere/carregue outro QR. A interceptacao
esta ativa e pode afetar esses caminhos; nao deve ser confundida com bloqueio
do TikTok. Nao houve retry, reload ou segunda navegacao comandada pelo worker.
O guard permanece no contexto de verificacao; so e removido por um futuro
comando explicito de login, que NAO foi executado agora.

## Evidencias do resultado

13:53:09: FRAME_NAVIGATED para /tiktokstudio.
13:53:11: outro evento de frame no mesmo caminho, originado pelo documento.
13:53:12: o documento solicitou navegacao principal para /login.
13:53:12: AUTH_ENTRY_BLOCKED para https://www.tiktok.com/login, main_frame=true.
REQUEST_FAILED: net::ERR_ABORTED e net::ERR_BLOCKED_BY_CLIENT, resource_type=document.
O segundo codigo e consequencia da protecao local, nao evidencia de banimento,
rate limit ou erro do servidor TikTok.

Nao foram observados HTTP 401/403/408/409/429/5xx no buffer desta coleta.
Nao foi capturado um status HTTP 302; o redirecionamento e comprovado pela
solicitacao de navegacao do documento, nao por um status HTTP inventado.

Console: uma mensagem info e quatro warnings, sem conteudo bruto por sanitizacao.
Um PAGE_ERROR generico foi registrado as 13:52:49, antes da navegacao Studio;
seu texto foi omitido e nao permite atribuir causa ao login.

Snapshot final 13:53:35: 1 contexto, 1 pagina; titulo www.tiktok.com;
readyState=complete, visibilityState=visible. URL final de esquema interno foi
omitida pelo sanitizador ([redacted-url]). Sem marcadores positivos de login.
Storage local/session e IndexedDB: SecurityError na pagina final apos bloqueio;
service workers: NotSupportedError. Nao significa que o storage do perfil sumiu.

O classificador generico do DOM retornou UNKNOWN nessa pagina bloqueada. A
classificacao final NEEDS_LOGIN usa evidencia adicional e especifica: Studio
solicitou /login e nao havia cookie de sessao reconhecido. Nao foi uma conversao
arbitraria de UNKNOWN em NEEDS_LOGIN.

## Cookies finais: somente metadados

Expires abaixo e Unix timestamp, exceto -1 (sessao).

| Name | Domain | Path | Expires | Secure | HttpOnly | SameSite |
| --- | --- | --- | --- | --- | --- | --- |
| [redacted-id] | .www.tiktok.com | / | 1814799179 | true | false | Lax |
| tiktok_webapp_theme | .www.tiktok.com | / | 1814799179 | true | false | Lax |
| ttwid | .tiktok.com | / | 1820415180.081014 | true | true | None |
| msToken | www.tiktok.com | / | 1796655184 | false | false | Lax |
| msToken | .tiktok.com | / | 1789749961.004827 | true | false | None |
| odin_tt | .tiktok.com | / | 1820422391.691406 | false | true | Lax |
| tt_csrf_token | .tiktok.com | / | -1 | true | true | Lax |

Nenhum valor foi registrado. O token CSRF surgido no acesso normal ao Studio
nao comprova sessao autenticada. session_cookie_present permaneceu false.

## Estado final e verificacoes

auth_status=NEEDS_LOGIN; session_verified=0; session_verified_at=null;
login_active=false. UI: Sessao TikTok requer login e botao Abrir login.
Detalhe registrado: Studio direcionou para login; entrada bloqueada antes de
carregar. Aguarda nova autorizacao.

26 testes mockados de diagnostico/inspecao passaram antes da ativacao.
Logs novos registram somente os dois POSTs de inspect-existing-session e GETs;
nenhum POST /login, /prepare ou /review.

Nenhum novo QR foi gerado/escaneado, nenhum login foi clicado, nenhum upload,
criacao de post, publicacao ou agendamento foi realizado. Encerrada a verificacao
com o browser atual preservado e sem iniciar outro teste.
