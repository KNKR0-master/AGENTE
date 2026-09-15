# Chrome manual e observabilidade - preparado em disco

Nenhum browser aberto, login iniciado, processo encerrado ou servidor reiniciado
nesta implementacao. Historico, JSONL do run 5db008ee, profile e SQLite de producao
nao foram modificados. Testes usam bases temporarias e processos mockados.

## Console

Eventos incluem timestamp, level, message sanitizada, source_url sanitizada,
line e column (indices fornecidos pelo Playwright, zero-based quando disponiveis).
Redacao granular de valores de credenciais, cookies, Authorization, tokens,
JWT/API keys, emails e blobs; query strings e fragmentos nao sao preservados.
Mensagens limitadas a 2000 caracteres, sem serializacao de console.args.
Pageerrors preservam tipo e mensagem sanitizada. Nao recupera retroativamente
as mensagens omitidas no run anterior.

## Polling

Endpoints reais identificados nos eventos:
- www.tiktok.com/passport/web/get_qrcode
- login-us.www.tiktok.com/passport/web/check_qrconnect
- login-eu.www.tiktok.com/passport/web/check_qrconnect
- web-sg.tiktok.com/passport/web/check_qrconnect

Nenhum corpo real disponivel no historico. Logo NENHUM campo de resposta foi
validado. VERIFIED_FIELDS permanece vazio, QR_RESPONSE_STATE registra UNKNOWN /
NO_VERIFIED_SCHEMA e body_read=false. Nenhum response.json/body e chamado.
Projetor futuro aceita somente paths e enums revisados, nunca string arbitraria;
fixtures dos testes sao sinteticas e nao representam schema TikTok.
Uma estrutura real fornecida de maneira segura sera necessaria antes de ativar
extracao semantica. Nao inferir sucesso por HTTP 200.

## Autorizacoes QR

experimental_qr_enabled=false, fail-closed quando ausente. Checado em authorize,
request e despacho controlado. Nenhum novo run_id criado nesta implementacao.
Codigo ainda nao ativado nos processos antigos; nao reinicia-los sem autorizacao.

## Chrome manual futuro

Comando previsto para a instalacao atual (via Popen com lista de argumentos,
shell=False; nao executar este comando nesta etapa):

```text
"C:\Program Files\Google\Chrome\Application\chrome.exe" "--user-data-dir=C:\Users\Usuario\Documents\New project 10\data\publisher\browser_profiles\a9a3acebc275495a8271b74e73faeab6" about:blank
```

Sem remote-debugging-pipe, no-sandbox, flags Playwright, scripts, DOM ou rede
interceptada. Nenhuma navegacao automatica ao TikTok. Chrome instalado e descoberto
nos diretorios padrao do Windows. O usuario faz toda a autenticacao e fecha Chrome.

## Exclusao mutua

publisher_profile_ownership no mesmo SQLite, chave por caminho absoluto
normalizado. BEGIN IMMEDIATE reserva MANUAL_STARTING ou PLAYWRIGHT_STARTING antes
de lancar. Outros processos/threads nao podem adquirir a mesma reserva.
Inventario Windows le command lines do Chrome e compara --user-data-dir por
argumento parseado, inclusive Chrome antigo/externo sem registro no SQLite.
Falha de inventario bloqueia a operacao. Nao fecha Chrome para liberar a trava.
Lease incerto apos erro/crash fica retido (fail-closed), sem limpeza automatica.
Processos externos lancados por outros programas nao obedecem ao SQLite; a
verificacao de inventario cobre os ja existentes, e Chrome mantem seu proprio
bloqueio nativo de profile. Nao prometer controle sobre programas externos.

MANUAL_BROWSER_ACTIVE bloqueia contextos Playwright e preparacao de upload.
PLAYWRIGHT_ACTIVE bloqueia abertura manual. Context.close libera sua propria
reserva pelo token de ownership, nunca a de outro dono.

## UI e verificacao posterior

Acoes: Abrir perfil para login manual; Verificar sessao; Diagnostico da sessao.
GET/refresh nao lanca Chrome. Botao manual depende da capacidade do novo servidor.
Nao acompanha DOM/rede durante uso manual. O estado so e reconciliado quando o
usuario solicita Verificar sessao: inventario local confirma fechamento e grava
SESSION_REQUIRES_VERIFICATION, sem escrever session_verified.
Entao o fluxo existente faz uma navegacao controlada ao Studio, no mesmo profile,
com sandbox e protecao para nao carregar login/QR automaticamente.
Somente evidencia positiva estavel no Studio pode persistir autenticacao.
Se Chrome manual ainda estiver aberto, verificacao e rejeitada antes de Playwright.

## Testes

python -m unittest tests.test_manual_profile_observability tests.test_auth_test_lock
node --check dashboard/publisher.js
node tests/publisher_diagnostics_ui_check.cjs
git diff --check

Tudo offline: nenhuma chamada TikTok, nenhuma autenticacao.
