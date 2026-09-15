# Inspecao preservando a tentativa QR existente

Data: 08/09/2026. Horarios dos logs locais em America/Sao_Paulo.
Somente leituras de processos, codigo, logs, metadados de arquivos, Preferences
e API local GET /api/publisher. Nenhum POST de login, reload, navegacao, nova aba,
fechamento, reinicio, limpeza, upload ou alteracao de session_verified foi feito.
Este documento e o unico arquivo criado nesta inspecao; codigo/configuracao intactos.

## Resultado objetivo: UNKNOWN

Nao foi possivel ler o DOM e o cookie jar vivos da instancia atual. Isso impede
classificar com evidencia AUTH_NOT_RECEIVED, AUTH_STATE_PRESENT_BUT_PAGE_STUCK
ou AUTHENTICATED. A confirmacao no celular foi informada pelo usuario; nao e
prova de que o desktop recebeu uma sessao. Uma pagina QR parada tampouco prova falha.

## 1. Cookies e storage

- SQLite da aplicacao: conta #1, auth_status=LOGIN_IN_PROGRESS,
  session_verified=0, session_verified_at=null, login_active=true.
- auth_detail: Aguardando confirmacao positiva na pagina.
- updated_at registrado: 08/09/2026 11:52:59 BRT.
- Cookies existe, tamanho observado 20.480 bytes. Duas tentativas de abertura
  SQLite em mode=ro, inclusive fora do sandbox de arquivos, retornaram
  OperationalError: unable to open database file. Nao foi copiado, desbloqueado,
  descriptografado ou aberto em modo de escrita. A causa exata da indisponibilidade
  nao foi determinada; o browser esta em uso.
- Nomes, dominios, paths, expiry, secure, httpOnly e sameSite atuais: indisponiveis.
  Nenhum valor de cookie foi impresso. Nao reutilizar os seis cookies de uma
  inspecao anterior como se fossem o estado desta tentativa.
- Diretorios Local Storage e Session Storage existem; chaves vivas nao acessiveis.
- Existe IndexedDB/https_www.tiktok.com_0.indexeddb.leveldb. Isso identifica uma
  origem persistida, nao os nomes/logica dos bancos, nem confirma autenticacao.
- Service Worker/Database, CacheStorage e ScriptCache existem; nao foi possivel
  enumerar registrations/workers ativos. A existencia da pasta nao prova worker ativo.
- O timestamp de arquivo/pasta nao demonstra conteudo autenticado.

## 2. Evidencia de autenticacao

Nenhuma evidencia suficiente obtida. session_verified=0 significa que o detector
nao confirmou os sinais exigidos, nao que uma sessao definitivamente nao existe.
Ele exige cookie conhecido e marcador positivo de UI em duas leituras. Na tela
de QR, cookies presentes sem marcador visivel continuariam como login em andamento.

## 3. DOM do QR

URL https://www.tiktok.com/login/qrcode e permanencia visual no QR: relato do usuario.
URL/titulo atuais, readyState, visibilityState, mensagens visiveis/ocultas, estados
escaneado/pendente/concluido/expirado/challenge: nao observados diretamente.
Nao atribuir nenhum desses estados com base no relato do celular apenas.

## 4. Console e erros da pagina

O worker nao registra console ou pageerror. Nao ha trace/HAR desta tentativa.
poll_login_sessions descarta excecoes do detector (worker.py:285) e apenas zera
o contador de estabilidade. Portanto, a ausencia de log nao exclui erro JS,
referencia perdida, frame substituido ou leitura DOM interrompida.
Nenhum novo listener foi injetado na instancia atual.

## 5. Requests, polling e redirects

Log local server-auth-20260908.err.log:
- 11:16:49 POST /api/publisher/login -> 200.
- 11:52:57 POST /api/publisher/login -> 200.
Esses sao comandos locais aceitos, nao respostas de autenticacao TikTok; nao
provam dois QR, dois contexts ou duas autenticacoes concluidas.

O worker faz leitura passiva do DOM/cookies aproximadamente a cada segundo;
nao implementa o polling QR do site. Nao ha listeners de response/requestfailed,
redirect, popup ou framenavigated gravando evidencias. Assim, nao foi possivel
confirmar polling ativo, 401/403/429/5xx, cancelamentos ou redirect interrompido.
O que ja aconteceu nao pode ser reconstruido com fidelidade a partir destes logs.

## 6. Topologia e acesso

- Python dashboard: PID 19176.
- Driver Playwright Node: PID 6532, filho de 19176.
- Chrome principal da conta: PID 16440, filho de 6532.
- Chrome observado: 151.0.7922.175, conforme comando do crashpad.
- Subprocessos associados: 6128 crashpad; 14424 GPU; 22896 network utility;
  18868 storage utility; 12284, 2988 e 17080 renderers.
- Ha UMA instancia principal Chrome com o userDataDir da Cecilia na enumeracao.
  Subprocessos renderers nao sao outras instancias e nao equivalem ao numero de abas.
- userDataDir:
  C:/Users/Usuario/Documents/New project 10/data/publisher/browser_profiles/a9a3acebc275495a8271b74e73faeab6
- O codigo mantem um contexto por account id e uma referencia de autenticacao.
  A API confirma login_active=true, mas nao expoe a identidade dessa referencia,
  contagem real de BrowserContexts, paginas, URLs ou titulos. Esses itens nao
  foram comprovados em runtime; nao inferir uma aba apenas pelo contrato do codigo.
- A instancia usa --remote-debugging-pipe; nao foi encontrado DevToolsActivePort.
  A ferramenta CUA so lista o browser interno do Codex, com Publisher Desk, e nao
  esta instancia Chrome. Nao foi criada uma conexao/perfil/aba para contornar isso.
- O canal pertence ao driver atual. Nao houve injecao em processos, tomada do pipe
  ou tentativa de conectar outro Playwright, pois isso arriscaria o estado atual.

## 7. Todos os argumentos do processo principal, na ordem observada

Executavel: C:/Program Files/Google/Chrome/Application/chrome.exe

```text
--disable-field-trial-config
--disable-background-networking
--disable-background-timer-throttling
--disable-backgrounding-occluded-windows
--disable-back-forward-cache
--disable-breakpad
--disable-client-side-phishing-detection
--disable-component-extensions-with-background-pages
--disable-component-update
--no-default-browser-check
--disable-default-apps
--disable-dev-shm-usage
--disable-edgeupdater
--disable-extensions
--disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,BoundaryEventDispatchTracksNodeRemoval,DestroyProfileOnBrowserClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,BlockOriginHeaderModificationOnRedirect,Translate,AutoDeElevate,OptimizationHints,msForceBrowserSignIn,msEdgeUpdateLaunchServicesPreferredVersion
--enable-features=CDPScreenshotNewSurface
--allow-pre-commit-input
--disable-hang-monitor
--disable-ipc-flooding-protection
--disable-popup-blocking
--disable-prompt-on-repost
--disable-renderer-backgrounding
--disable-updater-scheduler
--force-color-profile=srgb
--metrics-recording-only
--no-first-run
--password-store=basic
--use-mock-keychain
--no-service-autorun
--export-tagged-pdf
--disable-search-engine-choice-screen
--unsafely-disable-devtools-self-xss-warnings
--edge-skip-compat-layer-relaunch
--disable-infobars
--disable-search-engine-choice-screen
--disable-sync
--enable-unsafe-swiftshader
--no-sandbox
"--user-data-dir=C:\Users\Usuario\Documents\New project 10\data\publisher\browser_profiles\a9a3acebc275495a8271b74e73faeab6"
--remote-debugging-pipe
about:blank
```

Nao constam: --disable-setuid-sandbox, --disable-web-security,
--disable-site-isolation-trials, --disable-blink-features, --enable-automation,
--proxy-server, --proxy-bypass-list ou --host-resolver-rules.
O processo continua automatizado pelo pipe, mesmo sem --enable-automation.

## 8. Origem exata de --no-sandbox

src/publisher/worker.py:314 chama launch_persistent_context com profile,
channel=chrome, headless=False, viewport=None; nao fornece args ou chromium_sandbox.

O Playwright instalado, em:
C:/Users/Usuario/AppData/Local/Programs/Python/Python312/Lib/site-packages/playwright/driver/package/lib/coreBundle.js:43075
executa:

```javascript
if (options.chromiumSandbox !== true)
  chromeArguments.push("--no-sandbox");
```

Portanto a omissao de chromium_sandbox permite esse padrao do SDK. Sem justificativa
concreta neste projeto Windows, o flag fica RECOMENDADO PARA REMOCAO na proxima
execucao autorizada, via chromium_sandbox=True. Nada foi alterado agora.
Isso corrige a configuracao de seguranca; nao prova a causa do QR parado.

## 9. Interceptacao

Nao foram encontrados route/proxy/headers/UA customizados ou service_workers=block
no fluxo Publisher ativo. context.route e page.route encontrados pertencem aos
dois scripts de teste offline, que nao sao importados pelo worker de producao.
O launch observado nao possui flags de proxy/DNS. Preferences nao possui chave
proxy nem bloqueio JavaScript explicito nos campos inspecionados; ProxyEnable=0
na configuracao Windows consultada. PAC, politicas corporativas, antivirus e
interceptacao fora do processo nao foram exaustivamente auditados.

Os defaults --disable-features (incluindo ThirdPartyStoragePartitioning e
BlockOriginHeaderModificationOnRedirect) alteram comportamento do browser, mas
nao sao evidencia de request interception ativa nem de falha desta autenticacao.

## 10. Hipotese e limite

Comprovado: falta observabilidade para diferenciar sessao ausente de sessao
presente com pagina presa. A cadeia confirmacao no celular -> sessao desktop ->
redirect e o trecho a investigar. Nao ha fundamento para escolher como causa
mais provavel um 429, cookie bloqueado, proxy, erro JS ou --no-sandbox.
AUTH_STATE_PRESENT_BUT_PAGE_STUCK permanece hipotese, nao conclusao.

## 11. Correcao minima recomendada, nao aplicada

- Expor snapshot de diagnostico somente leitura pelo worker proprietario:
  contexts/pages, referencia auth, URL sanitizada/titulo, readyState/visibility,
  metadados de cookies sem valores, chaves de storage, IndexedDB e service workers.
- Separar classificacao diagnostica de session_verified: UNKNOWN se a coleta
  falhar; AUTH_NOT_RECEIVED apenas se snapshot completo nao mostrar estado de
  sessao; AUTH_STATE_PRESENT_BUT_PAGE_STUCK se ha estado de sessao mas QR persiste
  (presenca nao prova validade no servidor); AUTHENTICATED somente com evidencia
  positiva suficiente. Nunca promover verified pela mera presenca de cookie.
- Registrar erros de observacao em vez de silencia-los; capturar passivamente
  console/pageerror, response/requestfailed, navegacoes e popups, com sanitizacao.
  Listeners novos nao recuperam os eventos perdidos desta tentativa.
- Para proximo launch Windows: args adicionais do projeto = []; mesmo perfil,
  channel=chrome, headless=False, viewport=None, chromium_sandbox=True.
  Deixar userDataDir/pipe sob gestao do Playwright. Nao usar
  ignore_default_args=True nem remover todos os defaults sem avaliacao separada.

## 12. Proximo teste unico

Agora: nenhum novo login. A melhor proxima coleta e um snapshot SOMENTE LEITURA
da aba atual, caso o usuario disponibilize acesso pelo DevTools ja existente ou
autorize uma via segura de observacao. Nao e necessario repetir QR para isso.

Somente depois de autorizada a conclusao desta sessao e a instrumentacao futura:
mesmo perfil, sandbox ativado, listeners passivos instalados antes da navegacao;
verificar primeiro o estado persistido. Se a sessao ja estiver autenticada, nao
gerar QR. Se nao estiver, UMA tentativa manual, sem retry/reload/upload, registrando
o ponto exato entre scan, confirmacao, cookie, resposta e redirect. Nao executado.
