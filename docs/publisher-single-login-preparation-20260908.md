# Preparacao da tentativa unica

Status: implementado em disco e testado offline. NAO ativado no servidor atual.
Nenhuma navegacao, reload, login, QR, alteracao de profile ou de session_verified
foi realizada nesta preparacao. O browser aberto permanece intocado.

## Entrada deliberada

Depois de NOVA autorizacao, o worker atualizado aceita
`request_controlled_login(account_id, explicitly_authorized=True)`.
Nao e chamado pelo startup, GET diagnostico ou botao comum de login.
Nao foi adicionado endpoint publico que inicie a tentativa.
O servidor ainda em execucao precisa de ativacao controlada autorizada antes do teste.

Reserva por conta antes da fila; a reserva permanece consumida depois do resultado.
Chamadas posteriores, inclusive login comum, sao rejeitadas nessa execucao.
Operacoes de draft/review ficam bloqueadas durante o teste isolado.
Exige profile existente e exatamente uma pagina; nao consolida nem cria abas.
Reutiliza context/profile, Chrome visivel, viewport=None, chromium_sandbox=True.
Listeners sao instalados antes da unica navegacao para LOGIN_URL.
A protecao da inspecao anterior e removida somente nessa entrada autorizada.

## Evidencia

Arquivo local `data/publisher/evidence/login-{account_id}-{timestamp}.jsonl`.
Eventos passivos completos, respostas inclusive 2xx, metodos e relacoes de redirect.
Sem bodies, headers, argumentos de console, valores de cookies ou query strings.
Erros JS preservam tipo e momento, nao mensagem/stack potencialmente sensivel.
Snapshots incluem URL, titulo sanitizado, estado do documento, sinais semanticos,
cookies somente metadados, chaves de storage, IndexedDB/SW e eventos recentes.
Mudancas nos valores de storage/cookies NAO sao registradas nem inferidas a partir
de metadados iguais. O registro nao equivale a captura de bodies de respostas QR.

Marcos: LOGIN_PAGE_OPENED, QR_AVAILABLE (sinal semantico de leitura do QR),
QR_STATE_CHANGED, NAVIGATION_AFTER_CONFIRMATION, AUTH_STATE_CHANGED,
COOKIE_STORAGE_METADATA_CHANGED e FINAL_STATE.
Nao inventar um marco de scan/confirmacao se ele nao apareceu no DOM.
`report_phone_confirmation(account_id)` registra informacao humana como USER;
nao confirma autenticacao. DOM_CONFIRMATION_COMPLETE tem origem DOM separada.

## Parada

Teto total: 300 segundos desde inicio; depois de confirmacao: ate 60 segundos,
sempre respeitando o teto total. Poll passivo a cada ciclo do worker (~1 segundo,
mais duracao da coleta). HTTP 429 sinaliza parada imediatamente no listener;
finalizacao ocorre na proxima coleta, sem nova acao de browser.
Challenge, rejeicao explicita, QR expirado, popup ou topologia inesperada encerram.
Sem retries/reload/nova navegacao. Na falha, o browser fica intacto e a coleta do
teste para; scripts/polling proprios do TikTok nao sao interrompidos ou modificados.

AUTHENTICATED exige dois snapshots positivos consecutivos de pagina autenticada
com cookie de sessao reconhecido. So entao persiste verificacao e timestamp e
fecha graciosamente o contexto. Falha de fechamento e registrada, sem force kill.
Confirmacao no celular e cookies isolados nao significam sucesso.
Classificacoes: AUTHENTICATED, AUTH_NOT_RECEIVED,
AUTH_STATE_PRESENT_BUT_PAGE_STUCK, CHALLENGE, RATE_LIMITED, LOGIN_REJECTED, UNKNOWN.

## Relatorio posterior

Extrair timeline do JSONL, preservando origem USER/DOM e limitacoes da captura.
Informar classificacao, primeira divergencia observavel, HTTP/JS, mudancas de
metadados/storage, redirects observados, hipotese sustentada e proxima acao.
Nao tratar ausencia de corpo HTTP ou de sinal DOM como rejeicao comprovada.
