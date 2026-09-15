# Publisher Desk: catalogo interno e diagnostico estatico

Data: 07/09/2026. Fuso das horas apresentadas: America/Sao_Paulo.
Escopo: arquivos locais, SQLite, metadados de perfil e logs existentes.
Nao houve acesso ao TikTok, novo QR, login, upload, publicacao, agendamento,
limpeza de cookies ou alteracao da autenticacao nesta analise.

## A. Alteracoes do Publisher Desk

### Origem dos produtos antes da alteracao

Cadeia comprovada no codigo:

dashboard/publisher.js: refresh -> api -> renderProducts
-> GET /api/publisher
-> dashboard_server.Handler._api_get
-> PublisherAPI.get em src/publisher/http.py
-> PublisherService.snapshot/catalog em src/publisher/service.py
-> SQL direto, sem camada de repository separada
-> data/agent.db: products + publisher_products + product_aliases
-> sync_affiliate_product_catalog em src/product_catalog.py
-> affiliate_order_items + affiliate_orders
-> TikTokReportProvider / AffiliateReportImporter, relatórios Affiliate Orders XLSX.

Evidencias da base ANTES da migracao:

- 137 produtos, todos com product_key iniciado por tiktok:.
- Os 137 possuem referencia provider=tiktok_report em product_external_references.
- 144 aliases possuem source=TIKTOK_REPORT; 4 aliases possuem source=PUBLISHER.
- O catalogo Publisher usava LEFT JOIN com products: exibia toda a base anterior,
  mesmo quando publisher_products so tinha cinco registros de configuracao local.
- Os cinco registros locais eram os produtos 2, 43, 52, 54 e 64. Suas legendas
  existentes BODY SPLASH TESTE foram preservadas.
- Produto 2: Kit 3 Body Splash Agapis, ID legado 1735699075378742733.
  Produto criado em 09/08/2026 00:02:40; seus itens de pedido ja existiam em
  08/08/2026 21:30:37. Produto 43 (Body Splash Divine) tem a mesma origem temporal.
- Produto 64: Kit 3 Body Splash Agapis, ID legado 1736793747389842893,
  criado em 17/08/2026 07:23:29, apos itens importados as 07:23:27.
- Batch de importacao 1: affiliate_orders_7671532453370840839.xlsx,
  COMPLETED, 6.305 linhas, 63 produtos. Batch 5:
  affiliate_orders_7674352198335989522 2.xlsx, COMPLETED, 5.745 linhas.

O codigo que popula products agrupa itens por external_product_id, escolhe o
nome mais frequente dos pedidos e insere product_key=tiktok:<id>. Tambem grava
referencias, aliases e vinculos. AffiliateReportImporter.process_batch chama
essa sincronizacao apos importar. dashboard_server.main tambem a chama ao iniciar.
O watcher start_report_import_worker monitora arquivos locais e pode importar
novos relatorios; nao e uma chamada a API Creator para descobrir produtos.

Conclusao: os produtos reais foram reaproveitados do modulo comercial anterior,
alimentado por XLSX. Nao foram inventados pelo Publisher nem criados por seu seed.
Migracoes antigas adicionaram campos/tabelas, mas a rotina identificada de
populacao e a sincronizacao dos pedidos. As legendas e os aliases PUBLISHER sao
edicoes locais posteriores; o banco nao registra o autor de cada edicao.
Nao e possivel provar qual processo/usuario disparou cada insercao inicial,
pois nao existe trilha imutavel por produto, e imports atualizam os registros.
Essa ressalva nao altera a evidencia forte da fonte XLSX para os 137 produtos.

Antes, novos produtos podiam aparecer automaticamente no Publisher por causa do
LEFT JOIN sobre o catalogo comercial inteiro. Depois desta correcao, nao:
o Publisher trabalha com um snapshot editorial inicial e cadastros locais.
O Analytics pode continuar recebendo produtos de relatorios sem alterar esse snapshot.

### Modelo novo

Foi estendida publisher_products, sem apagar ou renomear registros de products:

- product_id: identidade interna, exposta como id no catalogo;
- name, short_name, default_caption, active, created_at, updated_at;
- account_id opcional, preservado por compatibilidade;
- legacy_source, legacy_product_id, legacy_shop_url: auditoria somente.

A migracao internal_catalog_v2, registrada em publisher_migrations, copia uma
unica vez os campos existentes, preservando as configuracoes locais. Reexecutar
init_publisher nao repete a copia nem importa novos produtos comerciais.
Novos produtos internos recebem um registro neutro em products, com chave
publisher:<uuid>, apenas para manter as FKs existentes. Suas edicoes editoriais
ficam em publisher_products e nao sobrescrevem campos do Analytics.
IDs/URLs comerciais nao sao exigidos nem consultados pelo worker.

### Legendas e lotes

Create Batch copia exclusivamente Product.default_caption para Draft.caption.
Um caption recebido arbitrariamente no payload de criacao nao substitui esse valor.
Alterar a legenda do produto depois nao modifica drafts existentes.
Editar draft, editar selecao e Usar legenda do produto (acao explicita) permitem
alterar apenas os drafts selecionados. Trocar produto na edicao nao substitui
silenciosamente a legenda existente.

Legenda base foi removida de Criar lote. Ha apenas um preview readonly da legenda
do produto selecionado. Produto sem legenda cria draft CAPTION_MISSING;
preparacao com legenda vazia e rejeitada antes de iniciar browser ou download.
CAPTION_MISSING tambem aparece ao limpar a legenda por edicao coletiva.

### Worker, revisao e consumo

Removidos do worker: Add/Search/Select/Confirm Product, validacao de Product ID,
exigencia de URL e verificacao de produto na reabertura do draft.
PRODUCT_NOT_FOUND e PRODUCT_NOT_CONFIRMED permanecem reconhecidos apenas para
historico/compatibilidade; nao sao emitidos pelo fluxo automatico atual.
product_required do preset fica como coluna legada, sem efeito no worker.

Novo fluxo: JIT -> upload/processamento -> Draft.caption -> configuracoes
suportadas -> confirmar persistencia/reabrir draft -> READY_FOR_REVIEW.
O produto do Desk e explicitamente interno. A revisao mostra o alerta:
"Produto TikTok Shop ainda precisa ser vinculado manualmente."
A confirmacao de registro publicado/agendado tambem pergunta sobre esse vinculo.
O registro local nao executa essa acao no TikTok.

Novo checkpoint destrutivo exige file, upload, caption, settings e durable,
ID/URL persistentes e evidencia de TIKTOK_DRAFT_CONFIRMED. Nao exige produto
comercial associado. Continua insuficiente apenas concluir setInputFiles.
Sucesso e commit no SQLite precedem CONSUMED_PENDING_DELETE -> deleteRemote ->
confirmacao de ausencia -> CONSUMED -> limpeza TEMP. Falha de exclusao mantem
o draft recebido; retry faz apenas limpeza. Idempotencia, quarentena de estado
incerto, locks do cache e processamento sequencial de lotes foram preservados.

### Arquivos e verificacao

Alterados nesta etapa: src/publisher/service.py, worker.py, lifecycle.py, http.py;
dashboard/publisher.html, publisher.js, publisher.css;
config/publisher_studio.json; testes Publisher e documentacao.
Nenhuma alteracao de logica nos importadores ou Analytics nesta etapa.

Testes locais: CRUD interno sem ID comercial; preservacao de campos Analytics;
snapshot de legenda e ausencia de alteracao retroativa; edicao individual/coletiva;
CAPTION_MISSING antes de browser; pausa bloqueando login/review/prepare;
checkpoint sem produto; retry de delete sem upload; migracao idempotente;
20 referencias remotas; UI responsiva e aviso de vinculo manual.
O page object foi testado somente com HTML interceptado e browser offline,
sem conexao ao TikTok. Perfis reais nao foram usados pelos testes.
Resultado: 27 testes unitarios Publisher passaram, mais os dois scripts de UI.
Suite geral: 103 testes, 101 passaram; permanecem as duas falhas antigas de
periodos fixos em test_dashboard_uses_requested_period e
test_daily_analytics_separates_gmv_commissions_received_and_content_type.
Antes da migracao foi criado backup SQLite em
data/publisher/backups/before-internal-catalog-20260907-190437.db.
Comparacao antes/depois confirmou integralmente preservadas as 137 linhas de
products, a conta #1 e o draft #1. A migracao preservou as cinco legendas locais.

## B. Diagnostico do TikTok Login

### Arquitetura encontrada

Publisher Desk -> POST /api/publisher/login com account id
-> get_row(publisher_accounts,id) -> worker.submit('login',id)
-> thread proprietaria do Playwright -> worker.context(account)
-> chromium.launch_persistent_context(userDataDir, channel='chrome', headless=False)
-> context.new_page -> goto('https://www.tiktok.com/login') -> bring_to_front.

Nao e contexto efemero. Usa Google Chrome instalado, nao o Chromium empacotado
como canal padrao. Existe um contexto por account id em memoria, reaproveitado
enquanto estiver aberto. Cada comando login, porem, abre uma NOVA aba.
Cada preparacao tambem abre sua propria aba; contas diferentes usam perfis distintos.

Conta real atual: id=1, CECILIA FIGUEIREDO, ativa, criada em 07/09 as 18:26:49.
userDataDir absoluto:

C:/Users/Usuario/Documents/New project 10/data/publisher/browser_profiles/a9a3acebc275495a8271b74e73faeab6

O diretorio existe e foi criado as 18:26:52. Seu caminho e gravado no account e
nao muda ao reiniciar o servidor. Um UUID novo so e gerado ao cadastrar OUTRA conta.
Existe somente esse diretorio de conta na raiz de perfis Publisher inspecionada.
Nao ha rotina Publisher que limpe perfis ou cookies. O coletor de cache atua apenas
em data/publisher/temp/job_*, nao em browser_profiles.

Default/Network, Local Storage, Session Storage, Sessions, History e Preferences
existem. Inspecao readonly do Cookies encontrou seis cookies TikTok, mas zero
nomes sessionid/sessionid_ss/sid_tt. Valores nao foram lidos ou descriptografados.
Isso e um indicio de sessao de usuario nao concluida/persistida, nao uma prova
absoluta de autenticacao ou nao autenticacao em todas as variantes da plataforma.
Existencia de Local Storage/Session Storage tambem nao prova login concluido.

### Evidencias de navegacao e tentativas

No log HTTP local preservado data/publisher/server.err.log:

- 18:26:51: POST /api/publisher/login -> 200;
- 18:29:51: POST /api/publisher/login -> 200;
- 18:30:27: POST /api/publisher/login -> 200;
- 18:31:53: POST /api/publisher/prepare -> 200;
- 18:35:51: POST /api/publisher/login -> 200.

200 significa comando aceito/enfileirado, NAO login confirmado. Nao existe log
de quem originou cada POST. O codigo nao tem retry automatico desses POSTs;
a UI os envia por clique. Cada comando pode produzir nova aba no mesmo perfil.

History do perfil registra /login/qrcode (visit_count=5), /login (7 e 1 em URLs
distintas), /login/phone-or-email e /login/phone-or-email/email, alem de upload (1).
Esses contadores nao equivalem necessariamente a tentativas de autenticacao
validadas pelo servidor. Query strings nao foram expostas no relatorio.

No SQLite, o unico draft real e #1, produto interno #43, BODY SPLASH TESTE.
A execucao do lote #1 esta PAUSED. Em 18:31:55 o draft terminou STUDIO_CHANGED:
"Mapeamento de upload/produto pendente de validacao real". Evidencia local:
data/publisher/evidence/1-ERROR-2026-09-07T21-31-55+00-00.png.
Isso confirma o bloqueio de configuracao daquela preparacao, nao sucesso do login.

Nao foram encontrados codigos 429/403 de rede TikTok, traces de redirect ou
respostas de autenticacao nesses logs Publisher. O alerta de excesso de tentativas
e informado pelo usuario; nao foi reproduzido nem transformado em um 429 presumido.
Na inspeção de processos nao havia Chrome com perfil Publisher/TikTok de teste
ativo. Havia somente o servidor dashboard, depois parado para aplicar a pausa.

### Respostas sobre o controle de autenticacao

- Nao existe retry automatico de login nem polling Publisher que recarregue QR.
- O polling de cinco segundos em publisher.js consulta SOMENTE GET local
  /api/publisher. Nao navega o TikTok nem verifica sessao autenticada.
- O loop ocioso do worker processa eventos Playwright; nao recarrega a pagina.
- Nao ha timeout que destrua automaticamente o contexto. goto tem timeout de
  navegacao do Playwright, mas o tratamento nao limpa o perfil nem fecha o contexto.
- Nao existe detector positivo e confiavel de login concluido no Publisher.
- NEEDS_LOGIN e inferido quando a URL contem /login durante challenge(), chamado
  no preparo. O comando login em si apenas abre a pagina e retorna ao loop.
- Ausencia de /login nao e prova de autenticacao. Uma URL de upload ou pagina
  parcialmente carregada pode ser interpretada cedo demais.
- Cookies/local storage nao sao usados pelo Publisher para atestar autenticacao.
- O JavaScript interno do site pode ter seu proprio polling; isso nao pode ser
  determinado por esta inspecao local e nao foi investigado com requisicoes novas.

O modulo antigo Analytics tem perfil independente em data/tiktok-browser-profile
e rotina propria de login/handoff. Ele pode abrir uma janela sem qualquer Account
do Publisher. Nao foi executado nem modificado nesta analise.

### Mensagens da UI

"Studio: validacao de persistencia pendente" e derivado dos flags validated e
durable_confirmation_validated no JSON. Trata da validacao tecnica de upload e
salvamento de drafts; NAO indica se cookies ou login foram persistidos.
Os flags continuam false. O novo aviso de pausa e separado dessa configuracao.

"Nenhuma conta cadastrada" aparece somente quando a resposta accounts esta vazia.
Agora a mesma base tem a conta #1 e a API consegue devolve-la. O primeiro comando
login registrado ocorreu dois segundos apos seu cadastro. No fluxo Publisher,
um id inexistente e rejeitado antes de abrir browser. Portanto nao ha evidencia
atual de abertura Publisher sem account; a tela vazia relatada pode ser anterior
ao cadastro, uma renderizacao desatualizada ou janela de outro modulo. Nao foi
reproduzida, e nao se pode escolher uma dessas explicacoes como fato.

Account e configuracao local (nome, perfil e ativo); nao e registro de sessao
autenticada. O diretorio guarda estado Chrome; pode conter dados de visitante.
Nenhum dos dois, isoladamente, comprova sucesso do login.

### Causa provavel, limites e proposta

O sintoma e compativel com uma transferencia de autenticacao QR que nao chegou
a estabelecer/redirecionar uma sessao utilizavel no desktop. As varias aberturas
de /login e QR podem ter agravado a limitacao relatada. O ponto comprovado no app
e a ausencia de acompanhamento positivo e de deduplicacao de comandos de login.
Isso explica sua falta de feedback, mas NAO prova a causa do QR travado no site.
Sem trace daquela tentativa nao e possivel atribuir a causa a cookies bloqueados,
rede, challenge, automacao ou politica do TikTok com seguranca.

Proposta minima, ainda NAO aplicada a autenticacao:
1. Reutilizar uma unica aba de login por account; bloquear cliques concorrentes.
2. Registrar login em andamento, URL sem tokens, navegacoes, timeout e resultado.
3. Detectar marcador positivo de sessao autenticada e redirecionamento concluido;
   nao usar apenas ausencia de /login nem o sucesso do QR no celular.
4. Aguardar essa unica tentativa sem reload/retry e sem destruir o contexto.

Riscos: nova tentativa prematura pode prolongar bloqueios; apagar o perfil perde
evidencias/sessao; outro processo no mesmo userDataDir pode conflitar; traces podem
conter tokens e devem ser locais e sanitizados; liberar flags de persistencia sem
validacao pode comprometer a seguranca da exclusao remota. Nada disso foi feito.

Proximo teste unico proposto, somente apos revisao deste relatorio, liberacao
explicita e ausencia do bloqueio informado pelo TikTok: usar o account #1 e o
MESMO perfil, uma unica aba, uma unica tentativa manual de login pelo metodo
escolhido pelo usuario; registrar navegacao/respostas sanitizadas e aguardar
marcador positivo de sessao. Sem recarregar QR, sem retry, sem upload. Se nao
concluir ou surgir challenge/limite, encerrar a tentativa e analisar o registro.
Nao ha prazo de espera inventado: o momento seguro depende do estado da plataforma.

### Contencao local aplicada

tiktok_actions_paused=true bloqueia login, review e prepare antes de iniciar
browser. Botoes de contato estao desabilitados. E uma pausa de seguranca solicitada,
nao uma mudanca de autenticacao: o codigo de login/contexto, cookies, pasta de
perfil e integracao Analytics foram preservados. Nenhuma tentativa pendente e
reiniciada automaticamente ao subir o dashboard local.
