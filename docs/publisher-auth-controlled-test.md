# Publisher: autenticacao manual controlada

## Nota de estado

Este documento registra a preparacao e os testes historicos. A autenticacao
manual e a persistencia da sessao foram posteriormente validadas; consulte
`docs/publisher-stable-checkpoint.md` para o comportamento atual.

Correcao incremental em 08/09/2026. Nenhum login real faz parte desta entrega.

## Reserva e reutilizacao

- O problema anterior era `context.new_page()` a cada comando login.
- `PublisherWorker` agora reserva a tentativa sob um `RLock` por account id,
  antes de colocar o comando na fila. O servidor existente tem um worker unico.
- Enquanto ha comando pendente, chamadas concorrentes retornam
  `LOGIN_ALREADY_IN_PROGRESS`. Com a pagina aberta, no maximo um comando de foco
  fica pendente. Playwright continua restrito a sua thread proprietaria.
- Reutiliza a aba registrada, uma aba TikTok restaurada ou a aba inicial em branco.
  Somente na ausencia delas cria uma pagina. Duplicatas restauradas de login sao
  consolidadas; abas de drafts e popups OAuth nao sao fechados por essa rotina.
- Timeout nao valida a sessao nem provoca reload/retry, mesmo se a aba ficar em
  branco. Fechar uma tentativa inconclusiva exige novo clique manual para reabrir.

## Detector e persistencia

`TikTokAuthDetector` le apenas o DOM e o cookie jar da pagina aberta manualmente.
Nao usa fetch, endpoints de autenticacao, reload, timeout ou desaparecimento de QR.
Exige duas observacoes positivas consecutivas e consistentes, combinando:

1. Cookie de sessao TikTok nao vazio e nao expirado, no dominio esperado; e
2. Area TikTok Studio com controle visivel de saida e sem controle de login; OU
   confirmacao explicita de sessao conectada na ponte de login do proprio TikTok.

Dominios externos, login ainda solicitado, challenge e sinais incompletos nao
confirmam autenticacao. O reconhecimento real do layout permanece sujeito ao
teste manual futuro; na ausencia dos sinais o detector falha de modo conservador.
Valores de cookies e query strings OAuth nao sao persistidos ou exibidos.

Campos aditivos em `publisher_accounts`: `auth_status`, `session_verified`,
`session_verified_at`, `auth_detail`. Sucesso observado grava verified=1; perda
observada de confirmacao grava verified=0, preservando a data historica.
Ao fechar a pagina autenticada/reiniciar, o ultimo sucesso confirmado permanece
registrado, com sua data na UI; nao representa nova verificacao online.
Uma tentativa interrompida no reinicio passa a NEEDS_LOGIN, sem ser retomada.

O browser_profile persistido e o mesmo, sem trocar userDataDir, limpar cookies,
Local Storage ou criar outro profile para a conta existente. A migracao nao
modifica produtos, midias, drafts ou credenciais do Drive.

## UI e liberacao

Estados: nao verificada, login em andamento, autenticada, requer login.
Botoes: Abrir login sem pagina ativa; Continuar login com tentativa ativa;
Abrir TikTok Studio apos confirmacao. Cliques simultaneos tambem sao deduplicados
no frontend. Atualizar/carregar o dashboard consulta somente a API local.

Depois dos testes offline, tiktok_actions_paused foi definido como false, sem
executar login. validated e durable_confirmation_validated continuam false: a preparacao
e persistencia de drafts nao foram validadas no site nesta entrega.

## Verificacao offline

- `python -m unittest tests.test_publisher_auth tests.test_publisher tests.test_publisher_remote`
- `python tests/publisher_auth_browser_check.py`
- `python tests/publisher_worker_browser_check.py`

Testes de autenticacao usam objetos Playwright falsos e SQLite isolado; sockets
sao bloqueados nos testes do worker. O teste da UI usa Chrome headless efemero,
offline, com todas as rotas interceptadas e respostas artificiais, sem servidor
real ou perfil da Cecilia. Verifica os quatro estados, debounce, feedback,
projecao DOM passiva e telas de 390, 768 e 1440 pixels.

Resultado: 48 testes unitarios passaram (47 na suite e mais o teste do contrato
HTTP isolado), assim como os dois scripts de navegador offline acima. Nenhuma
tentativa real de login foi realizada e nenhum QR foi solicitado.

## Arquivos desta correcao

- src/publisher/auth.py: detector, estados e persistencia.
- src/publisher/worker.py: lock, reserva, reutilizacao, foco e observacao passiva.
- src/publisher/service.py: quatro colunas aditivas da conta.
- src/publisher/http.py: snapshot da sessao e resposta estruturada de login.
- dashboard/publisher.js: estados, botoes e bloqueio de cliques simultaneos.
- dashboard/publisher.css: legibilidade dos estados de conta no mobile.
- config/publisher_studio.json: liberacao exclusivamente por acao manual.
- tests/test_publisher_auth.py: testes unitarios, concorrencia e HTTP simulado.
- tests/publisher_auth_browser_check.py: UI e DOM em navegador offline.
- docs/publisher-auth-controlled-test.md: registro da correcao.

Banco local: migracao aditiva em data/agent.db. Backup anterior em
data/publisher/backups/before-auth-fix-20260908-083222.db.

Validacao apos reinicio: GET local /api/publisher retorna pausa=false, conta #1
NOT_AUTHENTICATED, session_verified=0 e login_active=false. Comparacao com backup
preservou todas as colunas anteriores de publisher_accounts, publisher_products,
publisher_drafts e publisher_media_assets. Os 399 arquivos do perfil mantiveram
o mesmo hash agregado SHA-256 antes/depois:
476e413bdcc01d986828de5309d18154f7cda76a7f281a1a36ba66460b6a7b4e.
O total de logs LOGIN no banco permaneceu zero. Servidor local reiniciado na
porta 8765; nenhuma chamada real a POST /api/publisher/login foi feita.
