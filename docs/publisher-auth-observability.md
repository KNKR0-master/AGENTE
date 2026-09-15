# Observabilidade de login Publisher

## Estado atual

O codigo descrito neste documento foi ativado e a sessao persistente da Cecilia
foi validada em uma nova inicializacao do Publisher via Chrome + CDP. O fluxo
normativo atual esta em `docs/publisher-desk.md` e
`docs/publisher-stable-checkpoint.md`; as secoes de preparacao abaixo sao
historicas.

Implementacao em 08/09/2026. Escopo exclusivo: diagnostico read-only e listeners
passivos. A classe existente do projeto chama-se PublisherWorker; nao foi criada
outra classe de worker ou arquitetura paralela chamada TikTokStudioWorker.

## Ativacao historica

O servidor e o Chrome da tentativa preservada NAO foram reiniciados, recarregados
ou modificados em memoria. O codigo novo so entra em vigor na proxima ativacao
autorizada do servidor. Isso nao inicia login. O proximo launch manual autorizado
do Chrome usara chromium_sandbox=True e o mesmo perfil persistido.

Nao houve tentativa de hot patch, injecao ou tomada do pipe atual. A interface
consulta auth_diagnostics_available na resposta normal do servidor: em servidor
antigo o botao fica desabilitado. Nao existe polling automatico do novo endpoint.

## Contrato HTTP

GET /api/publisher/accounts/{id}/auth-diagnostics

Valida a conta usando SQLite mode=ro, nao executa init_db, retorna JSON com
Cache-Control: no-store e rejeita Origin divergente do Host local.
Nao chama worker.start, context, submit, new_page, goto, reload ou login.

get_auth_diagnostic_snapshot(account_id) coloca uma solicitacao numa fila
separada, limitada a oito itens. A thread Playwright ja existente processa a
leitura. Nao acessa objetos Playwright a partir da thread HTTP.
Espera de resposta limitada a cinco segundos; fila cheia/worker ocupado retorna
SNAPSHOT_BUSY/UNKNOWN, sem fallback de abertura. Um timeout da espera HTTP nao
interrompe forcadamente uma leitura ja iniciada na pagina.

Exemplo de formato, sem valores reais:

```json
{
  "schema_version": 1,
  "account_id": 1,
  "captured_at": "ISO-8601",
  "status": "OK",
  "diagnostic_state": "UNKNOWN",
  "context_count": 1,
  "page_count": 1,
  "auth_page_id": "page-1",
  "session_cookie_present": false,
  "cookies": [],
  "pages": [{
    "page_id": "page-1",
    "is_auth_page": true,
    "url": "https://www.tiktok.com/login/qrcode",
    "title": "Log in | TikTok",
    "readyState": "complete",
    "visibilityState": "visible",
    "login_state": "UNKNOWN",
    "dom_collected": true,
    "is_login_page": true,
    "semantic_signals": {"visible": [], "hidden": []},
    "localStorageKeys": [],
    "sessionStorageKeys": [],
    "indexedDB": [],
    "serviceWorkers": []
  }],
  "errors": [],
  "detector_error": null,
  "events": []
}
```

status: OK, PARTIAL, NO_ACTIVE_CONTEXT, WORKER_UNAVAILABLE, SNAPSHOT_BUSY ou
COLLECTION_FAILED. Sem contexto: NO_ACTIVE_CONTEXT, context_count=0,
page_count=0, pages=[], diagnostic_state=UNKNOWN. Contagem indisponivel por
worker ocupado e representada como null, nao como zero paginas comprovadas.

Cookies: apenas name, domain, path, expires, secure, httpOnly e sameSite.
Somente dominios TikTok entram na resposta. Valores sao usados transitoriamente
para testar presenca de cookie conhecido e nunca retornados ou registrados.
Storages: apenas nomes de chaves; IndexedDB: name/version; service workers:
scope/scriptURL sanitizados e state. Nao abre banco IndexedDB, registra worker
ou altera storage. Falhas/indisponibilidade das APIs aparecem em errors.

Cada erro inclui operation, error_type, page_id e mensagem fixa sanitizada.
Falha DOM/cookies ou erro recente do detector impede conclusao positiva. Falhas
em enumeracao opcional de storage tornam a coleta PARTIAL, mas nao apagam a
evidencia DOM/cookies que foi efetivamente obtida.

## Classificacao somente diagnostica

- UNKNOWN: evidencia critica indisponivel, pagina desconhecida, challenge/erro
  ou ultima observacao do detector com excecao.
- AUTH_NOT_RECEIVED: pagina de login reconhecida e ausencia dos cookies de sessao
  conhecidos na leitura completa. Significa ausencia de evidencia local conhecida,
  nao uma resposta oficial do servidor TikTok negando a autenticacao.
- AUTH_STATE_PRESENT_BUT_PAGE_STUCK: cookie conhecido nao vazio/nao expirado e
  pagina de login ainda reconhecida. Nao prova validade no servidor, nem que o
  cookie foi criado por esta tentativa especifica.
- AUTHENTICATED: cookie conhecido e pagina Studio com controle positivo de saida,
  sem marcador de login/challenge. Esta classificacao nao grava session_verified.

Estados semanticos: QR_WAITING_SCAN, QR_SCANNED, CONFIRMATION_PENDING,
CONFIRMATION_COMPLETE, QR_EXPIRED, CHALLENGE, ERROR, AUTHENTICATED_PAGE, UNKNOWN.
Retorna codigos canonicos, nao texto bruto da pagina. Mensagens ocultas ficam
separadas e nao sao utilizadas como prova positiva. Reconhecimento textual e
heuristico; nao equivale a confirmacao do protocolo de autenticacao.

## Eventos passivos

Listeners instalados uma vez no contexto e em suas paginas antes da navegacao
realizada pelo fluxo manual existente. Novas paginas/popups recebem listeners
sem que o diagnostico os crie. Eventos armazenados em memoria, maximo de 200 por
conta: CONSOLE, PAGE_ERROR, REQUEST_FAILED, HTTP_RESPONSE, FRAME_NAVIGATED,
URL_CHANGED, POPUP, PAGE_CLOSED, CONTEXT_CLOSED, COLLECTION_ERROR, LISTENER_ERROR,
DETECTOR_ERROR. Nao sobrevivem a reinicio; nao recuperam eventos passados.

HTTP_RESPONSE inclui 3xx, 401, 403, 408, 409, 429 e 5xx, apenas timestamp,
host/path sanitizados, status, resource_type e identificacao da pagina.
Nao le corpo ou headers. REQUEST_FAILED conserva apenas codigos de erro de
rede conhecidos; outros textos sao omitidos. Console conserva nivel, nao texto
ou argumentos arbitrarios. Erros conservam tipo conhecido, nao stack/mensagem
bruta. Fechamento registra causa UNKNOWN: nao presume que foi o usuario ou falha.

URLs perdem userinfo, query e fragment; segmentos dinamicos de path sao omitidos.
Nomes/titulos recebem redacao de padroes sensiveis e limite de tamanho. Omissao
intencional reduz detalhes do diagnostico para evitar vazamento de segredos.

poll_login_sessions agora registra a excecao sanitizada e mantem override
diagnostico UNKNOWN ate uma observacao subsequente bem-sucedida. Nao transforma
essa excecao em NEEDS_LOGIN nem escreve session_verified no caminho de erro.
O detector de autenticacao anterior continua separado; o novo GET/classificador
nao invoca persist_auth nem altera registros da conta.

## Sandbox futuro

launch_persistent_context(profile, channel="chrome", headless=False,
viewport=None, chromium_sandbox=True). Nenhum ignore_default_args=True.
Nao alterados userDataDir, cookies, storage, argumentos da instancia atual
ou configuracao do perfil. Nenhum browser foi iniciado para testar esse ajuste.

## Arquivos alterados

- src/publisher/diagnostics.py
- src/publisher/worker.py
- src/publisher/http.py
- dashboard_server.py
- dashboard/publisher.js
- dashboard/publisher.html
- dashboard/publisher.css
- tests/test_publisher_diagnostics.py
- tests/publisher_diagnostics_ui_check.cjs
- tests/publisher_diagnostic_dom_check.cjs
- docs/publisher-auth-observability.md

## Verificacao

21 testes Python mockados aprovados:
python -m unittest tests.test_publisher_diagnostics -v

Dois testes Node VM/DOM/fetch simulados aprovados, sem browser:
node tests/publisher_diagnostics_ui_check.cjs
node tests/publisher_diagnostic_dom_check.cjs

Cobrem paginas unica/multiplas, contexto ausente, falhas DOM/cookies/storage,
classificacao, sanitizacao, listeners idempotentes, buffer limitado, leitura na
thread proprietaria, timeout sem fallback, endpoint/routing read-only, preservacao
integral de conta de fixture, Cache-Control, sandbox futuro mockado, sete estados
semanticos reais do script, mensagens ocultas e UI sem POST/login.

Compilacao Python e git diff --check passaram (Git apenas avisou sobre CRLF).
Nenhum teste de navegador real, login, QR, upload ou chamada TikTok foi executado.
Nenhuma migracao ou escrita na base real foi realizada nesta etapa.
