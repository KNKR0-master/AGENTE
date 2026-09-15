# Trava persistente de autenticacao

Preparada em disco; nao houve ativacao, Chrome ou login nesta alteracao.

Fonte de verdade: tabela publisher_auth_test_runs em data/agent.db.
authorize_controlled_login exige autorizacao explicita e cria UUID sem iniciar worker.
request_controlled_login exige esse run_id e autorizacao explicita; consome via
UPDATE condicional em transacao BEGIN IMMEDIATE antes de enfileirar.
AUTHORIZED -> RESERVED -> RUNNING -> estado terminal, sem retorno.
Indice unico parcial impede duas autorizacoes/execucoes ativas por conta.
claim_execution impede que despacho duplicado alcance o browser.

Estados terminais imutaveis: AUTHENTICATED, RATE_LIMITED, CHALLENGE,
LOGIN_REJECTED, AUTH_NOT_RECEIVED, AUTH_STATE_PRESENT_BUT_PAGE_STUCK, UNKNOWN.
Excecoes do worker/coleta encerram UNKNOWN; nunca reenfileiram login.
Timeout da observacao permanece em cinco minutos. Reservas interrompidas ficam
consumidas; nova autorizacao explicita encerra reservas antigas vencidas como
UNKNOWN antes de criar outro UUID. Startup nao retoma execucoes.
Login comum tambem consulta historico persistente, impedindo bypass apos restart.
Refresh/restart do frontend nao concede autorizacao nem modifica o ledger.

Testes: python -m unittest tests.test_auth_test_lock (8 testes offline).
Nenhuma autorizacao de teste real foi criada na base de producao.
