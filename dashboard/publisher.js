const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = {drafts:[],products:[],accounts:[],presets:[],batches:[],logs:[],review_sessions:[],account_protection:[],library:{groups:[],page:1,pages:0,total_groups:0},folderCatalog:[]};
const uiState = window.PublisherUIState.create();
let selected = new Set(), productSelected = new Set(), editing = [], librarySelected = uiState.librarySelected, reviewing = null, libraryPage = 1;
let historyState = {executions:[],totalCount:0,nextBeforeId:null,hasMore:false,initialized:false,loadedMore:false,loading:false};
let historyExpanded = false, renamingBatchId = null, renamingBatchValue = '', configuringBatchId = null, queueRenderDeferred = false;
const expandedHistoryExecutions = new Set(), expandedQueueDraftDetails = new Set();
const expandedLibraryGroups = new Set(), libraryGroupPages = new Map();
const productPickerStates = new Map(), productPickerComponents = new Map();
let activeProductPickerKey = null, libraryRenderDeferred = false;
let libraryRequestSequence = 0;
let productScope = 'current', productWorkspace = {products:[],counts:{},repeated_name_groups:0}, productWorkspaceRequest = 0;
let folderCatalogSelected = new Set(), activeFolderCatalogGroup = 'probable_matches';
let driveSyncRecord = {}, driveSyncResult = null, driveSyncPhase = 'IDLE', driveSyncBusy = false;
const libraryBatchPanelState = {preview:null,request:0,submitting:false,resumeAfterFix:false,filterSnapshot:null};
let libraryBatchPreviewTimer = null;
const loginRequests = new Set();
const authLabels = {NOT_AUTHENTICATED:'não verificada',LOGIN_IN_PROGRESS:'login em andamento',AUTHENTICATED:'autenticada',NEEDS_LOGIN:'requer login'};
function renderAccounts(){
 $('#accountList').innerHTML=state.accounts.map(a=>{
  if(a.session_verified)a={...a,auth_status:'AUTHENTICATED'};
  const button='Abrir perfil para login manual';
  const manualBusy=['MANUAL_STARTING','MANUAL_BROWSER_ACTIVE','PLAYWRIGHT_STARTING','PLAYWRIGHT_ACTIVE'].includes(a.profile_state);
  const disabled=!state.worker_config?.manual_profile_available||!a.active||manualBusy||loginRequests.has(a.id);
  return `<div class="record"><div>${esc(a.name)} ${a.active?'':'(inativa)'}<small>${esc(a.browser_profile)}</small><small role="status">Sessão TikTok: ${authLabels[a.auth_status]||authLabels.NOT_AUTHENTICATED}</small><small>${esc(a.profile_state||'IDLE')}</small>${a.auth_detail?`<small>${esc(a.auth_detail)}</small>`:''}${a.session_verified_at?`<small>Última confirmação: ${esc(new Date(a.session_verified_at).toLocaleString())}</small>`:''}</div><button data-account-edit="${a.id}">Editar</button><button data-login="${a.id}" ${disabled?'disabled':''} aria-busy="${loginRequests.has(a.id)}">${button}</button><button data-verify-manual="${a.id}" ${state.worker_config?.manual_profile_available&&['MANUAL_BROWSER_ACTIVE','SESSION_REQUIRES_VERIFICATION'].includes(a.profile_state)?'':'disabled'}>Verificar sessão</button><button data-auth-diagnostic="${a.id}" ${state.worker_config?.auth_diagnostics_available?'':'disabled title="Servidor atual ainda sem diagnóstico"'}>Diagnóstico da sessão</button></div>`;
 }).join('')||'<p>Nenhuma conta cadastrada.</p>';
}
async function openAccountLogin(id){
 if(loginRequests.has(id))return;
 loginRequests.add(id);renderAccounts();
 try{
  await api('/open-manual-profile',{id});
  await refresh();
  notice('Perfil aberto no Chrome manual.');
 }finally{loginRequests.delete(id);renderAccounts();}
}
let diagnosticAccount = null;
async function showAuthDiagnostics(id){
 diagnosticAccount=id;
 const dialog=$('#authDiagnosticDialog'),body=$('#authDiagnosticBody');
 if(!dialog.open)dialog.showModal();
 body.textContent='Consultando estado do worker…';
 $('#refreshAuthDiagnostic').disabled=true;
 try{
  const snapshot=await api(`/accounts/${id}/auth-diagnostics`);
  const authPage=snapshot.pages.find(p=>p.is_auth_page);
  body.innerHTML=`<dl class="auth-diagnostic-fields"><dt>Estado técnico</dt><dd>${esc(snapshot.status)}</dd><dt>Diagnóstico</dt><dd>${esc(snapshot.diagnostic_state)}</dd><dt>Contextos / páginas</dt><dd>${esc(snapshot.context_count)} / ${esc(snapshot.page_count??'desconhecido')}</dd><dt>Página de autenticação</dt><dd>${esc(snapshot.auth_page_id??'não identificada')}</dd><dt>URL atual</dt><dd>${esc(authPage?.url??'indisponível')}</dd><dt>Documento</dt><dd>${esc(authPage?.readyState??'desconhecido')} / ${esc(authPage?.visibilityState??'desconhecido')}</dd><dt>Estado do login</dt><dd>${esc(authPage?.login_state??'UNKNOWN')}</dd><dt>Coleta</dt><dd>${esc(snapshot.captured_at)}</dd></dl><h3>Erros recentes</h3><pre>${esc(JSON.stringify([...(snapshot.errors||[]),...(snapshot.detector_error?[snapshot.detector_error]:[]),...(snapshot.events||[]).filter(e=>['PAGE_ERROR','DETECTOR_ERROR','COLLECTION_ERROR','LISTENER_ERROR','REQUEST_FAILED'].includes(e.event)||e.level==='error')],null,2))}</pre><h3>Navegação e rede</h3><pre>${esc(JSON.stringify(snapshot.events||[],null,2))}</pre><details><summary>Páginas, cookies e armazenamento</summary><pre>${esc(JSON.stringify({pages:snapshot.pages,cookies:snapshot.cookies,session_cookie_present:snapshot.session_cookie_present},null,2))}</pre></details>`;
 }catch(error){body.textContent=error.message;throw error;}
 finally{$('#refreshAuthDiagnostic').disabled=false;}
}
const field = (form,name) => form.elements.namedItem(name);
const productLabel = p => `${p.name} [${p.id}]`;
const chosenProduct = text => state.products.find(p => productLabel(p) === text);
const productDisplayName = product => product?.short_name?.trim() || product?.name || 'Sem produto';
const friendlyLibraryReason = value => ({CAPTION_MISSING:'Legenda do produto não configurada',PRODUCT_UNASSIGNED:'Produto da pasta não associado'})[String(value||'').trim()] || String(value||'').replace(/\s*\((CAPTION_MISSING|PRODUCT_UNASSIGNED)\)\s*/g,'').trim();
let noticeTimer;
const renderNotice = () => {
 const message=uiState.visibleMessage();
 $('#notice').textContent=message?.text||'';
 $('#notice').className=message?.error?'error':'';
 $('#notice').dataset.scope=message?.scope||'';
};
const notice = (text,error=false,scope='general') => { clearTimeout(noticeTimer);uiState.setMessage(scope,text,error);renderNotice();if(!error)noticeTimer=setTimeout(()=>{uiState.clearMessage(scope);renderNotice();},4500); };
const clearNotice = scope => { uiState.clearMessage(scope); renderNotice(); };
function mountProductCombobox(container,{input,results,hidden,context='',key=container?.dataset.productPickerKey||''}){
 if(!container||!input||!results||!hidden||input.dataset.comboboxReady)return;
 const pickerKey=String(key||'product-picker');
 const picker=productPickerStates.get(pickerKey)||{open:false,query:'',active:false,focused:false,selectedId:'',selectedLabel:'',activeIndex:-1};
 productPickerStates.set(pickerKey,picker);
 if(picker.selectedId){hidden.value=picker.selectedId;input.value=picker.selectedLabel;input.dataset.selectedLabel=picker.selectedLabel;}
 else if(picker.query)input.value=picker.query;
 input.dataset.comboboxReady='true';let current=[];
 const close=({rerender=true}={})=>{
  picker.open=false;picker.active=false;picker.focused=false;picker.activeIndex=-1;
  results.hidden=true;input.setAttribute('aria-expanded','false');
  if(activeProductPickerKey===pickerKey)activeProductPickerKey=null;
  if(rerender&&libraryRenderDeferred)queueMicrotask(()=>{if(!activeProductPickerKey&&libraryRenderDeferred){libraryRenderDeferred=false;renderLibrary();}});
 };
 const draw=()=>{
  picker.query=input.value;current=PublisherUIState.searchProducts(state.products,picker.query,context,10);
  picker.activeIndex=Math.min(picker.activeIndex,current.length-1);
  results.innerHTML=current.map((product,index)=>`<button type="button" role="option" data-combo-index="${index}" aria-selected="${index===picker.activeIndex}"><strong>${esc(productDisplayName(product))}</strong>${product.short_name&&product.name!==product.short_name?`<small>${esc(product.name)}</small>`:''}</button>`).join('')||'<span class="empty-mini">Nenhum produto ativo encontrado</span>';
  results.hidden=false;input.setAttribute('aria-expanded','true');picker.open=true;picker.active=true;activeProductPickerKey=pickerKey;
 };
 const choose=product=>{picker.selectedId=String(product.id);picker.selectedLabel=productDisplayName(product);picker.query=picker.selectedLabel;hidden.value=picker.selectedId;input.value=picker.selectedLabel;input.dataset.selectedLabel=input.value;close();};
 productPickerComponents.set(pickerKey,{container,input,results,draw,close});
 input.addEventListener('focus',()=>{picker.focused=true;picker.active=true;activeProductPickerKey=pickerKey;draw();});
 input.addEventListener('input',()=>{picker.query=input.value;if(input.value!==input.dataset.selectedLabel){hidden.value='';picker.selectedId='';picker.selectedLabel='';input.dataset.selectedLabel='';}draw();});
 input.addEventListener('blur',()=>{picker.focused=false;});
 input.addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();close();return;}
  if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();if(results.hidden)draw();picker.activeIndex=Math.max(0,Math.min(current.length-1,picker.activeIndex+(event.key==='ArrowDown'?1:-1)));draw();results.querySelector(`[data-combo-index="${picker.activeIndex}"]`)?.scrollIntoView({block:'nearest'});}
  if(event.key==='Enter'&&!results.hidden&&current.length){event.preventDefault();choose(current[Math.max(0,picker.activeIndex)]);}
 });
 results.addEventListener('click',event=>{const option=event.target.closest('[data-combo-index]');if(option)choose(current[Number(option.dataset.comboIndex)]);});
}
function closeProductPicker(key,{rerender=true}={}){productPickerComponents.get(String(key))?.close({rerender});}
document.addEventListener('click',event=>{if(!activeProductPickerKey)return;const picker=productPickerComponents.get(activeProductPickerKey);if(picker&&!picker.container.contains(event.target))closeProductPicker(activeProductPickerKey);});
document.addEventListener('focusin',event=>{if(!activeProductPickerKey)return;const picker=productPickerComponents.get(activeProductPickerKey);if(picker&&!picker.container.contains(event.target))closeProductPicker(activeProductPickerKey);});
function refreshActiveProductPicker(){if(!activeProductPickerKey)return;const picker=productPickerComponents.get(activeProductPickerKey);if(picker?.container.isConnected&&productPickerStates.get(activeProductPickerKey)?.open)picker.draw();}
async function api(path='',data){const r=await fetch('/api/publisher'+path,data===undefined?{}:{method:'POST',headers:data instanceof FormData?{}:{'Content-Type':'application/json'},body:data instanceof FormData?data:JSON.stringify(data)});const body=await r.json();if(!r.ok)throw Error(body.error||'Erro na operação');return body;}
function libraryFilterParams(includeGroupView=false){const params=new URLSearchParams({search:$('#librarySearch')?.value||'',path:$('#libraryPath')?.value||'',product_id:$('#libraryProduct')?.value||'',type:$('#libraryType')?.value||'VIDEO',status:$('#libraryStatus')?.value||'',decision:$('#libraryDecision')?.value||''});if(includeGroupView)params.set('group_view',$('#libraryGroupView')?.value||'ACTIVE');return params;}
async function loadLibrary(){
 const request=++libraryRequestSequence,params=libraryFilterParams(true);
 params.set('page',String(libraryPage));params.set('page_size','20');
 const response=await fetch('/api/publisher/library/groups?'+params),body=await response.json();
 if(request!==libraryRequestSequence)return false;
 if(!response.ok){const error=Error(body.error||'Falha ao consultar Biblioteca');error.operationScope='library';throw error;}
 state.library=body;libraryGroupPages.clear();clearNotice('library');
 const visible=new Set((body.groups||[]).map(group=>group.group_key));
 for(const [key,picker] of productPickerStates){
  if(key.startsWith('folder:')&&!visible.has(key)){
   if(activeProductPickerKey===key)closeProductPicker(key,{rerender:false});
   picker.open=false;picker.active=false;picker.focused=false;
   productPickerComponents.delete(key);productPickerStates.delete(key);
  }
 }
 for(const group of body.groups||[]){
  if(request!==libraryRequestSequence)return false;
  if(expandedLibraryGroups.has(group.group_key))await loadLibraryGroup(group.group_key,1,request);
 }
 return request===libraryRequestSequence;
}
async function loadLibraryGroup(key,page=1,request=libraryRequestSequence){
 const params=libraryFilterParams(true);params.set('group_key',key);params.set('page',String(page));params.set('page_size','50');
 const response=await fetch('/api/publisher/library/group-items?'+params),body=await response.json();
 if(request!==libraryRequestSequence)return false;
 if(!response.ok)throw Error(body.error||'Falha ao carregar vídeos do grupo');
 for(const item of body.items||[])if(item.status==='REMOTE_UNAVAILABLE')uiState.setLibrarySelection(item.drive_file_id,false);
 const old=libraryGroupPages.get(key);
 libraryGroupPages.set(key,{...body,items:page>1&&old?[...old.items,...body.items]:body.items});
 return true;
}
function visibleLibraryItems(){return (state.library?.groups||[]).flatMap(group=>expandedLibraryGroups.has(group.group_key)?libraryGroupPages.get(group.group_key)?.items||[]:[]);}
async function loadProductWorkspace(){const request=++productWorkspaceRequest,params=new URLSearchParams({scope:productScope,search:$('#productSearch').value});const response=await fetch('/api/publisher/products/workspace?'+params);const body=await response.json();if(!response.ok)throw Error(body.error||'Falha ao consultar produtos');if(request!==productWorkspaceRequest)return;productWorkspace=body;renderProductWorkspace();}
async function loadFolderCatalog(){const response=await fetch('/api/publisher/folder-catalog?grouped=1');const body=await response.json();if(!response.ok)throw Error(body.error||'Falha ao consultar pastas');state.folderCatalog=body;folderCatalogSelected.clear();renderFolderCatalog();}
const folderGroupLabels={probable_matches:'Matches prováveis',duplicates:'Duplicatas',medium:'Medium',ambiguous:'Ambíguas',containers_to_confirm:'Containers a confirmar',structurally_likely_products:'Produtos prováveis por estrutura'};
function renderFolderCatalog(){const catalog=state.folderCatalog||{groups:{},counts:{},current_product_containers:[]};const groups=catalog.groups||{};const counts=catalog.counts||{};if(!groups[activeFolderCatalogGroup])activeFolderCatalogGroup=Object.keys(groups)[0]||'probable_matches';$('#folderCatalogCounters').innerHTML=Object.keys(folderGroupLabels).map(key=>`<button class="${key===activeFolderCatalogGroup?'selected':''}" data-catalog-group="${key}"><span>${esc(folderGroupLabels[key])}</span><strong>${esc(counts[key]||0)}</strong></button>`).join('');$('#folderCatalogContainers').innerHTML=(catalog.current_product_containers||[]).map(container=>`<article><div><strong>${esc(container.folder_name)}</strong><small>${esc(container.full_path)}</small><span>${container.product_children.length} filho(s) produto · ${container.unclassified_children.length} aguardando revisão · ${container.direct_video_count} vídeo(s) direto(s)</span><small>${esc(container.reason)} · ${esc(container.confidence)}</small></div><label class="check" title="Configuração preparada, mas não habilitada nesta fase"><input type="checkbox" disabled ${container.auto_create_new_direct_children?'checked':''}>Criação automática de novos filhos diretos: ${container.auto_create_new_direct_children?'ativada':'desativada'}</label></article>`).join('')||'<p>Nenhum container de produto confirmado.</p>';const rows=groups[activeFolderCatalogGroup]||[];$('#folderCatalogSelection').textContent=`${folderCatalogSelected.size} selecionadas em ${folderGroupLabels[activeFolderCatalogGroup]}`;$('#folderCatalogGroups').innerHTML=`<div class="table-wrap"><table><thead><tr><th></th><th>Pasta do Drive</th><th>Inventário</th><th>Produto sugerido</th><th>Motivo</th><th>Ações</th></tr></thead><tbody>${rows.map(row=>{const candidates=row.existing_product_candidates||[];return `<tr><td><input type="checkbox" data-folder-review-select="${esc(row.drive_folder_id)}" ${folderCatalogSelected.has(String(row.drive_folder_id))?'checked':''} aria-label="Selecionar ${esc(row.name)}"></td><td><strong>${esc(row.name)}</strong><small>${esc(row.relative_path)}</small><small>ID: ${esc(row.drive_folder_id)}</small></td><td>${esc(row.direct_video_count)} vídeo(s)<small>${esc(row.direct_subfolder_count)} subpasta(s)</small></td><td>${candidates.map(candidate=>`<strong>${esc(candidate.name)}</strong><small>${esc(candidate.short_name||'')}</small><small>${(candidate.folder_associations||[]).length} associação(ões) de pasta</small>`).join('')||'Nenhum'}</td><td>${esc(row.structural_reason||row.role_reason||'Revisão humana pendente')}</td><td><div class="catalog-actions">${row.suggested_product_id?`<button data-folder-link="${esc(row.drive_folder_id)}" data-product-id="${esc(row.suggested_product_id)}">Vincular ao existente</button><button data-folder-reject-match="${esc(row.drive_folder_id)}">Não são o mesmo</button>`:''}<button data-folder-create="${esc(row.drive_folder_id)}">Criar produto</button><button data-folder-role="PRODUCT" data-folder-id="${esc(row.drive_folder_id)}">Produto</button><button data-folder-role="CATEGORY" data-folder-id="${esc(row.drive_folder_id)}">Categoria</button><button data-folder-role="PRODUCT_CONTAINER" data-folder-id="${esc(row.drive_folder_id)}">Container</button><button data-folder-role="UNCLASSIFIED" data-folder-id="${esc(row.drive_folder_id)}">Revisar depois</button></div></td></tr>`}).join('')||'<tr><td colspan="6">Nenhuma pasta neste grupo.</td></tr>'}</tbody></table></div>`;}
function options(items,empty='Selecionar'){return `<option value="">${empty}</option>`+items.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('');}
function fillSelect(el,items,empty){const old=el.value;el.innerHTML=options(items,empty);el.value=old;}
async function refresh(){try{state={...state,...await api()};clearNotice('health');}catch(error){error.operationScope='health';throw error;}try{await loadLibrary();}catch(error){error.operationScope=error.operationScope||'library';throw error;}render();await loadProductWorkspace();}
function view(name){document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===name));document.querySelectorAll('nav [data-view]').forEach(x=>x.classList.toggle('selected',x.dataset.view===name));}
document.querySelectorAll('[data-view]').forEach(x=>x.onclick=()=>view(x.dataset.view));
document.querySelectorAll('[data-close]').forEach(x=>x.onclick=()=>$('#'+x.dataset.close).close());
function render(){
 const system=state.system_status||{};$('#workerStatus').textContent=state.worker_config?.tiktok_actions_paused?'Contato com TikTok pausado · Diagnóstico de login':(system.system_label||'Sistema em verificação');
 $('#systemStatus').innerHTML=`<div><strong>${esc(system.system_label||'Sistema em verificação')}</strong><span>${esc(system.account_label||'Sessão não verificada')}</span><span>${esc(system.drive_label||'Drive em verificação')}</span></div><details><summary>Detalhes técnicos</summary><small>ffprobe: ${esc(state.worker_config?.ffprobe_version||state.worker_config?.ffprobe_error||'desconhecido')} · Drive: ${esc(state.drive_health?.diagnosis||'desconhecido')}</small></details>`;
 const protection=(state.account_protection||[]).find(item=>item.blocked),protectionBox=$('#accountProtection'),protectionView=PublisherUIState.protectionPresentation(protection);protectionBox.hidden=!protectionView;protectionBox.innerHTML=protectionView?`<div><strong>${esc(protectionView.title)}</strong><span>${esc(protectionView.message)} ${esc(protectionView.nextAction)}</span><button data-review-protection="${protection.account_id}">Revisar estado</button><details><summary>Detalhes técnicos</summary>${esc(protection.state)} · ${esc(protection.reason_code||'motivo não informado')} · ${esc(protection.source||'fonte não informada')}</details></div>`:'';
 renderLibrary();
 renderDriveSync();
 renderFolderCatalog();
 renderBatchSummary();
 $('#executionProgress').innerHTML=(state.executions||[]).slice(0,3).map(e=>`<div class="record"><div>Execução #${e.id} · Lote ${e.batch_id??'múltiplo'} · ${esc(e.status)}<small>${e.succeeded} preparados · ${e.failed} falharam · ${e.waiting_manual} aguardam ação · ${e.processing} processando · ${e.queued} na fila</small><progress value="${e.succeeded+e.failed+e.waiting_manual}" max="${e.total}"></progress> ${e.succeeded+e.failed+e.waiting_manual} / ${e.total}<small>Drive: ${e.remote_deleted} removidos · ${e.remote_preserved} preservados · ${e.remote_delete_pending} limpezas pendentes</small></div></div>`).join('')+`<small>Cache temporário: ${state.temporary_files||0} arquivos</small>`;
 $('#productOptions').innerHTML=state.products.filter(p=>p.active).map(p=>`<option value="${esc(productLabel(p))}">${esc(p.short_name)} ${esc(p.aliases.join(' '))}</option>`).join('');
 document.querySelectorAll('select[name=account_id]').forEach(el=>{fillSelect(el,state.accounts.filter(a=>a.active),'Selecionar / manter');if(!el.value&&el.closest('#batchForm')&&state.preferences?.default_publisher_account_id)el.value=String(state.preferences.default_publisher_account_id);});
 document.querySelectorAll('select[name=preset_id]').forEach(el=>fillSelect(el,state.presets,'Padrão / manter'));
 fillSelect($('#filter-account'),state.accounts,'Todas');fillSelect($('#filter-batch'),state.batches,'Todos');
 fillSelect($('#filter-product'),state.products,'Todos os produtos');
 fillSelect($('#libraryProduct'),[...state.products.filter(p=>p.active),{id:'UNASSIGNED',name:'Sem produto'}],'Todos os produtos');
 mountProductCombobox($('#libraryProductCombobox'),{input:$('#libraryAssignProductSearch'),results:$('#libraryProductResults'),hidden:$('#libraryAssignProduct')});
 const status=$('#filter-status').value;$('#filter-status').innerHTML='<option value="">Todos ativos</option>'+state.statuses.map(s=>`<option value="${esc(s)}">${esc(state.queue_status_labels?.[s]||'Estado a verificar')}</option>`).join('');$('#filter-status').value=status;
 renderQueue();
 renderExecutionHistory();
 renderAccounts();
 $('#presetList').innerHTML=state.presets.map(p=>`<div class="record"><div>${esc(p.name)}<small>${esc(p.visibility)} · Comentários ${p.comments?'ON':'OFF'}</small></div><button data-preset-edit="${p.id}">Editar</button></div>`).join('');
 $('#logList').innerHTML=state.logs.map(l=>`<div class="log"><small>${esc(new Date(l.created_at).toLocaleString())} · Draft ${l.draft_id??'—'} · ${esc(l.step)} / ${esc(l.status)}</small><p>${esc(l.message)}</p>${l.screenshot_path?`<a target="_blank" href="/api/publisher?evidence=${l.id}">Ver evidência</a>`:''}</div>`).join('')||'<p>Nenhuma execução.</p>';
}
function queueFilters(){return {search:$('#queueSearch').value,status:$('#filter-status').value,product:$('#filter-product').value,account:$('#filter-account').value,batch:$('#filter-batch').value,readiness:$('#filter-readiness').value};}
function queueGroups(){return PublisherUIState.queueGroups(state,queueFilters());}
function visibleDrafts(){return queueGroups().flatMap(group=>group.drafts);}
const queueBlockerLabels={DRAFT_NOT_STARTABLE:'Este item ainda não pode ser preparado',CAPTION_MISSING:'Edite a legenda',MEDIA_NOT_AVAILABLE:'Mídia indisponível',BLOCKED_BY_ACCOUNT_PROTECTION:'Conta temporariamente protegida',NEEDS_LOGIN:'Conta precisa de login',STUDIO_CHANGED:'Studio precisa de revisão',MANUAL_ACTION_REQUIRED:'Verificação manual necessária'};
function queueBlocker(code){return queueBlockerLabels[code]||'Revise os dados deste item';}
function selectedDrafts(){return state.drafts.filter(d=>selected.has(d.id));}
function queueActionState(capability){const rows=selectedDrafts();return {ok:PublisherUIState.bulkAllowed(rows,capability),blocked:rows.filter(d=>!d.queue_capabilities?.[capability]).length};}
function renderQueue(){
 const container=$('#queueBatches'),activeElement=document.activeElement;
 if(activeElement?.matches('[data-batch-rename-input]')&&container.contains(activeElement)){queueRenderDeferred=true;return;}
 for(const detail of container.querySelectorAll('[data-queue-details]'))detail.open?expandedQueueDraftDetails.add(detail.dataset.queueDetails):expandedQueueDraftDetails.delete(detail.dataset.queueDetails);
 const allGroups=PublisherUIState.queueGroups(state),activeIds=new Set(allGroups.flatMap(group=>group.all.map(d=>d.id)));
 selected=new Set([...selected].filter(id=>activeIds.has(id)));
 const groups=queueGroups(),rows=groups.flatMap(group=>group.drafts),chosen=selectedDrafts();
 $('#count').textContent=`(${rows.length} vídeos · ${groups.length} grupos)`;$('#selectedCount').textContent=`${selected.size} selecionados`;$('#selectAll').checked=rows.length>0&&rows.every(d=>selected.has(d.id));
 const blockedAccount=(state.account_protection||[]).some(item=>item.blocked),runtimeBlocked=state.worker_config?.persistence_workflow_version!==1||!!state.worker_config?.tiktok_actions_paused;
 $('#bulkEdit').disabled=!queueActionState('can_edit').ok;$('#useProductCaption').disabled=!queueActionState('can_edit').ok;$('#archive').disabled=!queueActionState('can_remove').ok;
 $('#prepare').disabled=blockedAccount||runtimeBlocked||!queueActionState('can_start').ok||chosen.some(d=>!state.queue_preflights?.[d.batch_id]?.items?.find(item=>item.draft_id===d.id)?.ready);
 $('#duplicate').disabled=chosen.length!==1;
 const impeded=['can_edit','can_remove','can_start'].map(capability=>queueActionState(capability).blocked);$('#queueSelectionReason').textContent=chosen.length&&Math.max(...impeded)>0?`${Math.max(...impeded)} item(ns) impedem algumas ações`:'';
 if(!groups.length){const anyActive=allGroups.length>0;container.innerHTML=anyActive?'<p class="empty">Nenhum item corresponde aos filtros.</p>':'<div class="queue-empty"><p>Nenhum lote ativo.</p><button type="button" data-queue-go-library class="primary">Ir para Biblioteca</button></div>';return;}
 container.innerHTML=groups.map(group=>{
  const batch=state.batches.find(item=>item.id===group.batchId),execution=group.execution,caps=batch?.queue_capabilities||{};
  const groupIds=new Set(group.all.map(draft=>draft.id)),rawPre=group.source==='pending'?state.queue_preflights?.[group.batchId]:null;
  const preItems=(rawPre?.items||[]).filter(item=>groupIds.has(item.draft_id));
  const pre={total_items:group.all.length,items:preItems,ready_items:preItems.filter(item=>item.ready).length,
    blocked_items:preItems.filter(item=>!item.ready).length,warning_items:preItems.filter(item=>item.warnings?.length).length};
  const running=execution&&['QUEUED','RUNNING','PROCESSING'].includes(execution.status),paused=execution&&['PAUSED','PAUSED_MANUAL_ACTION_REQUIRED'].includes(execution.status);
  const batchLabel=group.source==='execution'?(running?'Em execução':paused?'Pausado':'Requer ação'):
    group.source==='pending'?(pre.blocked_items?'Requer correção':'Pronto para preparar'):
      group.all.some(d=>['MANUAL_ACTION_REQUIRED','NEEDS_LOGIN','STUDIO_CHANGED','FAILED','UPLOAD_FAILED'].includes(d.status)||d.upload_status==='TRANSFER_UNKNOWN'||d.media_status==='NEEDS_RECONCILIATION')?'Requer ação':
      group.all.some(d=>d.persistence_status==='PERSISTED'||d.status==='READY_FOR_REVIEW')?'Pronto para revisão':'Aguardando revisão';
  const startBlocked=group.source!=='pending'||pre.blocked_items>0||blockedAccount||runtimeBlocked||!group.all.every(d=>d.status==='DRAFT'&&d.queue_capabilities?.can_start);
  const progress=execution?`<div class="queue-progress"><progress value="${Number(execution.succeeded||0)+Number(execution.failed||0)+Number(execution.waiting_manual||0)}" max="${Number(execution.total||1)}"></progress><span>Execução #${execution.id} · ${Number(execution.succeeded||0)} concluídos · ${Number(execution.processing||0)} em andamento · ${Number(execution.queued||0)} aguardando · ${Number(execution.waiting_manual||0)} em revisão</span></div>`:'';
  const pauseCode=paused?state.execution_items?.find(item=>item.execution_id===execution.id&&item.error_code)?.error_code:null;
  const batchTitle=group.source==='execution'&&group.batchId==null?`Execução #${execution.id} · múltiplos lotes`:batch?.name||`Lote ${group.batchId??'sem lote'}`;
  const renameTitle=renamingBatchId===group.batchId?`<form class="queue-rename-form" data-batch-rename-form="${group.batchId}"><input data-batch-rename-input data-batch-id="${group.batchId}" aria-label="Nome do lote" maxlength="160" required value="${esc(renamingBatchValue||batch?.name||'')}"><button type="submit" class="primary">Salvar</button><button type="button" data-batch-rename-cancel>Cancelar</button></form>`:`<div class="queue-batch-title"><h3>${esc(batchTitle)}</h3>${group.source==='pending'&&caps.can_rename?`<button type="button" data-batch-rename="${group.batchId}" title="Renomear lote" aria-label="Renomear ${esc(batchTitle)}">Renomear</button>`:''}</div>`;
  const batchControls=group.source==='pending'?`<div class="queue-batch-controls">${caps.can_edit_configuration?`<button type="button" data-batch-configure="${group.batchId}">Editar conta/preset</button>`:''}${caps.can_cancel?`<button type="button" data-batch-cancel="${group.batchId}">Cancelar lote</button>`:''}</div>`:'';
  const summary=group.source==='pending'?`${pre.total_items} Draft(s) · ${pre.ready_items} pronto(s) · ${pre.blocked_items} precisam de correção${pre.warning_items?` · ${pre.warning_items} com aviso`:''}`:
    `${group.all.length} Draft(s) · ${group.source==='execution'?'ordem congelada da execução':'revisão pendente'}`;
  const startButton=group.source==='pending'?`<button type="button" data-start-batch="${group.batchId}" class="primary" ${startBlocked?'disabled':''} title="${startBlocked&&pre.blocked_items?`${pre.blocked_items} itens precisam ser corrigidos antes de continuar`:''}">Preparar lote</button>`:'';
  return `<section class="queue-batch" data-queue-batch="${esc(group.key)}" aria-label="${esc(batchTitle)}"><div class="queue-batch-heading"><div>${renameTitle}<p>${summary}</p></div><span class="queue-state">${batchLabel}</span>${startButton}${batchControls}</div>${pauseCode?`<p class="queue-batch-warning">${esc(queueBlocker(pauseCode))}</p>`:''}${group.source==='pending'&&pre.blocked_items?`<p class="queue-batch-warning">${pre.blocked_items} Draft(s) precisam ser corrigidos antes de continuar.</p>`:''}${progress}<div class="queue-items">${group.drafts.map((d,index)=>{
   const draftCaps=d.queue_capabilities||{},item=pre.items.find(candidate=>candidate.draft_id===d.id),position=group.frozen?group.positions.get(d.id):d.queue_position,display=d.queue_display||{label:'Estado a verificar',kind:'attention',message:''};
   const reordering=group.source==='pending'&&group.fullyVisible&&caps.can_reorder&&group.all.every(draft=>draft.queue_capabilities?.can_reorder);
   const thumbnail=d.thumbnail_link?`<img class="queue-thumbnail" src="${esc(d.thumbnail_link)}" alt="Prévia de ${esc(d.filename)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><span class="queue-thumbnail queue-placeholder" hidden aria-hidden="true">▶</span>`:'<span class="queue-thumbnail queue-placeholder" aria-hidden="true">▶</span>';
   const blockers=item?.blockers||[],warnings=item?.warnings||[];
   const readiness=item?(item.ready?(warnings.length?`${warnings.length} aviso(s)`:'Pronto'):`${blockers.length} bloqueio(s)`):display.label;
   const verifyLabel=d.media_status==='NEEDS_RECONCILIATION'?'Verificar / Reconciliar':'Verificar status';
   const actions=[draftCaps.can_edit?`<button data-edit="${d.id}">Editar</button>`:'',draftCaps.can_verify?`<button data-review="${d.id}" data-verification="true">${verifyLabel}</button>`:'',d.persistence_status==='PERSISTED'?`<button data-review="${d.id}">Revisar</button>`:'',draftCaps.can_remove?`<button data-remove-draft="${d.id}">Remover da fila</button>`:'',d.persistence_status==='PERSISTED'&&['RESERVED','CONSUMED_PENDING_DELETE'].includes(d.media_status)?`<button data-cleanup="${d.id}" ${runtimeBlocked?'disabled':''}>Finalizar consumo da mídia</button>`:''].filter(Boolean).join('')||'<span>Ação manual necessária</span>';
   const detailKey=execution?`${execution.id}:${d.id}`:`draft:${d.id}`;
   return `<article class="queue-item ${display.kind==='running'?'queue-current':''}" data-queue-draft="${d.id}" data-execution-id="${execution?.id||''}" ${reordering?'draggable="true"':''}><div class="queue-order"><strong>${esc(position??'?')}</strong>${reordering?`<div class="queue-move"><button type="button" data-move="up" data-draft="${d.id}" ${index===0?'disabled':''} title="Mover para cima" aria-label="Mover ${esc(d.filename)} para cima">↑</button><button type="button" data-move="down" data-draft="${d.id}" ${index===group.drafts.length-1?'disabled':''} title="Mover para baixo" aria-label="Mover ${esc(d.filename)} para baixo">↓</button><span class="queue-drag" title="Arrastar para reordenar" aria-hidden="true">⋮⋮</span></div>`:''}</div><input type="checkbox" data-select="${d.id}" ${selected.has(d.id)?'checked':''} aria-label="Selecionar ${esc(d.filename)}"><div class="queue-media">${thumbnail}<div><strong>${esc(d.filename)}</strong><small>${esc(d.account_name)}</small></div></div><div class="queue-product"><strong>${esc(d.product_short_name||d.product_name)}</strong><small>${esc(d.caption||'Legenda não definida')}</small></div><div class="queue-status"><span class="queue-state ${esc(display.kind)}">${esc(display.label)}</span><span>${esc(readiness)}</span>${display.message?`<small>${esc(display.message)}</small>`:''}</div><div class="queue-item-actions">${actions}</div><details class="queue-details" data-queue-details="${esc(detailKey)}" ${expandedQueueDraftDetails.has(detailKey)?'open':''}><summary>Detalhes</summary>${blockers.length?`<p>${blockers.map(code=>esc(queueBlocker(code))).join(' · ')}</p>`:''}${warnings.length?`<p>${warnings.map(code=>esc(queueBlocker(code))).join(' · ')}</p>`:''}<details><summary>Detalhes técnicos</summary><small>Draft ${d.id} · Lote ${d.batch_id??'—'} · Execução ${execution?.id||'—'} · ${esc(d.status)} · ${esc(d.error_code||'sem erro')} · ${esc(JSON.stringify({blockers,warnings,checkpoints:d.checklist||{}}))}</small></details></details></article>`;
  }).join('')}</div></section>`;
 }).join('');
 queueRenderDeferred=false;
}
function renderExecutionHistory(){
 const incoming=state.history||{executions:[],total_count:0,has_more:false,next_before_id:null};
 if(!historyState.initialized){historyState={...historyState,executions:incoming.executions||[],totalCount:Number(incoming.total_count||0),nextBeforeId:incoming.next_before_id,hasMore:!!incoming.has_more,initialized:true};}
 else{
  const merged=new Map(historyState.executions.map(execution=>[execution.id,execution]));
  for(const execution of incoming.executions||[])merged.set(execution.id,execution);
  historyState.executions=[...merged.values()].sort((a,b)=>b.id-a.id);
  historyState.totalCount=Number(incoming.total_count||0);
  if(!historyState.loadedMore){historyState.nextBeforeId=incoming.next_before_id;historyState.hasMore=!!incoming.has_more;}
 }
 for(const detail of $('#queueHistoryEntries').querySelectorAll('[data-history-execution]'))detail.open?expandedHistoryExecutions.add(Number(detail.dataset.historyExecution)):expandedHistoryExecutions.delete(Number(detail.dataset.historyExecution));
 const section=$('#queueHistorySection'),outer=$('#queueHistory');
 section.hidden=historyState.totalCount===0;
 $('#queueHistoryLabel').textContent=`Histórico (${historyState.totalCount})`;
 outer.open=historyExpanded;
 $('#queueHistoryLoadMore').hidden=!historyState.hasMore;
 $('#queueHistoryLoadMore').disabled=historyState.loading;
 $('#queueHistoryLoadMore').textContent=historyState.loading?'Carregando…':'Carregar mais histórico';
 $('#queueHistoryEntries').innerHTML=historyState.executions.map(execution=>{
  const name=execution.batch_names?.length===1?execution.batch_names[0]:execution.batch_names?.length?`${execution.batch_names.length} lotes`:execution.batch_id==null?'múltiplos lotes':`Lote ${execution.batch_id}`;
  const when=execution.finished_at||execution.started_at;
  const items=execution.items||[];
  return `<details class="queue-history-execution" data-history-execution="${execution.id}" ${expandedHistoryExecutions.has(execution.id)?'open':''}><summary>Execução #${execution.id} · ${esc(name)} · ${esc(when?new Date(when).toLocaleString():'data não informada')} · ${Number(execution.total||items.length)} Draft(s)</summary><p>${Number(execution.succeeded||0)} concluídos · ${Number(execution.failed||0)} falharam · ${Number(execution.waiting_manual||0)} aguardaram ação</p><ul>${items.map(item=>`<li data-history-item-key="${execution.id}:${item.draft_id}"><strong>${esc(item.filename||`Draft ${item.draft_id}`)}</strong> · ${esc(item.item_status||'estado não informado')}<small>${esc(item.batch_name||'Lote sem nome')} · ${esc(item.product_short_name||item.product_name||'Produto não informado')} · Draft ${item.draft_id} · posição ${item.execution_position??'—'}${item.item_error_code?` · ${esc(item.item_error_code)}`:''}</small></li>`).join('')||'<li>Nenhum Draft vinculado a esta execução.</li>'}</ul></details>`;
 }).join('')||'<p class="empty-mini">Nenhuma execução concluída.</p>';
 $('#queueHistoryEntries').querySelectorAll('[data-history-execution]').forEach(detail=>detail.addEventListener('toggle',()=>detail.open?expandedHistoryExecutions.add(Number(detail.dataset.historyExecution)):expandedHistoryExecutions.delete(Number(detail.dataset.historyExecution))));
}
async function loadMoreExecutionHistory(){
 if(historyState.loading||!historyState.hasMore||!historyState.nextBeforeId)return;
 historyState.loading=true;renderExecutionHistory();
 try{
  const params=new URLSearchParams({before_id:String(historyState.nextBeforeId),limit:'30'});
  const response=await fetch('/api/publisher/execution-history?'+params,{cache:'no-store'}),body=await response.json();
  if(!response.ok)throw Error(body.error||'Não foi possível carregar o histórico');
  const merged=new Map(historyState.executions.map(execution=>[execution.id,execution]));
  for(const execution of body.executions||[])merged.set(execution.id,execution);
  historyState.executions=[...merged.values()].sort((a,b)=>b.id-a.id);
  historyState.totalCount=Number(body.total_count||historyState.totalCount);historyState.nextBeforeId=body.next_before_id;
  historyState.hasMore=!!body.has_more;historyState.loadedMore=true;
 }finally{historyState.loading=false;renderExecutionHistory();}
}
function visibleProducts(){return productWorkspace.products||[];}
function renderProductFolders(product){const folders=product?.folders||[];$('#productFolders').innerHTML=folders.map(folder=>`<div class="folder-association"><span><strong>${esc(folder.name)}</strong><small>${esc(folder.relative_path)}</small></span><button type="button" data-remove-product-folder="${esc(folder.id)}">Remover</button></div>`).join('')||'<p class="empty-mini">Nenhuma pasta associada.</p>';const options=state.drive_folders||[];$('#productFolderSelect').innerHTML='<option value="">Selecionar pasta...</option>'+options.filter(folder=>!folders.some(current=>current.id===folder.id)).map(folder=>`<option value="${esc(folder.id)}">${esc(folder.relative_path||folder.name)}</option>`).join('');}
function renderProductEditor(product){const form=$('#productForm');form.dataset.productId=product?.id||'';renderProductFolders(product);$('#productEditorContext').textContent=product?`${product.current_media_count||0} mídia(s) atual(is) · ${product.active?'Ativo':'Arquivado'}`:'';}
function renderProducts(){const rows=visibleProducts(),counts=productWorkspace.counts||{};productSelected=new Set([...productSelected].filter(id=>rows.some(p=>p.id===id)));$('#productSelectedCount').textContent=`${productSelected.size} selecionados`;$('#selectAllProducts').checked=rows.length>0&&rows.every(p=>productSelected.has(p.id));for(const [scope,id] of [['current','productCurrentCount'],['no_current','productNoCurrentCount'],['archived','productArchivedCount']])$('#'+id).textContent=counts[scope]||0;$('#productRepeatedCount').textContent=productWorkspace.repeated_name_groups||0;document.querySelectorAll('[data-product-scope]').forEach(button=>button.classList.toggle('selected',button.dataset.productScope===productScope));$('#productScopeNote').textContent=productScope==='no_current'?'Produtos sem pasta ou mídia atual podem ter histórico e continuar válidos.':productScope==='repeated'?'Nomes iguais não significam o mesmo produto. Compare IDs, pastas e histórico antes de decidir.':'';$('#archiveProducts').disabled=!productSelected.size||[...productSelected].some(id=>!rows.find(p=>p.id===id)?.can_archive);$('#restoreProducts').disabled=!productSelected.size||[...productSelected].some(id=>!rows.find(p=>p.id===id)?.can_restore);$('#deleteProducts').disabled=!productSelected.size||[...productSelected].some(id=>!rows.find(p=>p.id===id)?.can_hard_delete);$('#productList').innerHTML=rows.map(p=>`<div class="record product-record"><input type="checkbox" data-product-select="${p.id}" ${productSelected.has(p.id)?'checked':''} aria-label="Selecionar ${esc(p.name)}"><div><strong>${esc(p.short_name||p.name)}</strong><small>ID ${p.id}${p.short_name?' · '+esc(p.name):''} · ${p.active?'Ativo':'Arquivado'} · ${p.has_default_caption?'Legenda configurada':'Legenda não configurada'}</small><small>${p.folder_count} pasta(s) · ${p.current_media_count} mídia(s) atual(is) · ${p.has_historical_references?'Com histórico':'Sem histórico'}</small>${p.folders?.length?`<small title="Pastas associadas">${esc(p.folders.map(f=>f.relative_path||f.name).join(' · '))}</small>`:''}${p.possible_duplicate_name?'<small>Nomes repetidos: revisar IDs e histórico</small>':''}</div><button type="button" data-product-edit="${p.id}">Configurar</button></div>`).join('')||'<p class="empty">Nenhum produto neste grupo.</p>';}
function renderProductWorkspace(){renderProducts();const blocked=[...productSelected].filter(id=>!visibleProducts().find(p=>p.id===id)?.can_hard_delete).length;if(blocked){const note=$('#productScopeNote');note.textContent+=(note.textContent?' ':'')+`${blocked} produto(s) têm histórico e não podem ser excluídos permanentemente. Você pode arquivá-los.`;}if(productScope!=='repeated')return;const list=$('#productList'),rows=visibleProducts(),nodes=[...list.querySelectorAll('.product-record')],groups=new Map();rows.forEach((product,index)=>{const key=product.duplicate_group||product.name;if(!groups.has(key))groups.set(key,{name:product.name,nodes:[]});groups.get(key).nodes.push(nodes[index]);});list.replaceChildren();for(const group of groups.values()){const section=document.createElement('section');section.className='product-duplicate-group';const title=document.createElement('h3');title.textContent=group.name;section.append(title);const grid=document.createElement('div');grid.className='product-duplicate-pair';group.nodes.forEach(node=>grid.append(node));section.append(grid);list.append(section);}}
 function renderCaptionPreview(){}
function fillForm(form,data){for(const [k,v] of Object.entries(data)){const el=field(form,k);if(!el)continue;if(el.type==='checkbox')el.checked=!!v;else el.value=Array.isArray(v)?v.join(', '):(v??'');}}
function formData(form){const data=Object.fromEntries(new FormData(form));form.querySelectorAll('input[type=checkbox]').forEach(x=>data[x.name]=x.checked);return data;}
async function task(fn,scope='general'){try{await fn();}catch(e){notice(e.message,true,e.operationScope||scope);}}
$('#refresh').onclick=()=>task(refresh);
$('#refreshAuthDiagnostic').onclick=()=>task(()=>showAuthDiagnostics(diagnosticAccount));
document.addEventListener('click',e=>{const button=e.target.closest('[data-auth-diagnostic]');if(button)task(()=>showAuthDiagnostics(Number(button.dataset.authDiagnostic)));});
document.addEventListener('click',e=>{const button=e.target.closest('[data-verify-manual]');if(button&&!button.disabled)task(async()=>{await api('/verify-manual-profile',{id:Number(button.dataset.verifyManual)});await refresh();});});
['queueSearch','filter-status','filter-product','filter-readiness','filter-account','filter-batch'].forEach(id=>$('#'+id).oninput=renderQueue);
$('#queueHistory').addEventListener('toggle',event=>{historyExpanded=event.currentTarget.open;});
$('#queueHistoryLoadMore').onclick=()=>task(loadMoreExecutionHistory,'queue');
$('#queueBatches').addEventListener('toggle',event=>{const detail=event.target.closest('[data-queue-details]');if(detail)detail.open?expandedQueueDraftDetails.add(detail.dataset.queueDetails):expandedQueueDraftDetails.delete(detail.dataset.queueDetails);});
$('#queueBatches').addEventListener('input',event=>{if(event.target.matches('[data-batch-rename-input]'))renamingBatchValue=event.target.value;});
$('#queueBatches').addEventListener('focusout',event=>{if(!event.target.matches('[data-batch-rename-input]'))return;setTimeout(()=>{if(queueRenderDeferred&&!$('#queueBatches').contains(document.activeElement))renderQueue();},0);});
$('#queueBatches').addEventListener('submit',event=>{const form=event.target.closest('[data-batch-rename-form]');if(!form)return;event.preventDefault();task(async()=>{const input=form.querySelector('[data-batch-rename-input]'),id=Number(form.dataset.batchRenameForm),name=input.value.trim();await api(`/batches/${id}/rename`,{name});renamingBatchId=null;renamingBatchValue='';input.blur();await refresh();notice('Lote renomeado');},'queue');});
$('#batchConfigurationForm').onsubmit=event=>{event.preventDefault();task(async()=>{if(!configuringBatchId)throw Error('Lote não selecionado');const form=event.currentTarget,data={};for(const key of ['account_id','preset_id']){const value=field(form,key).value;if(value)data[key]=Number(value);}await api(`/batches/${configuringBatchId}/configuration`,data);$('#batchConfigurationDialog').close();configuringBatchId=null;await refresh();notice('Conta/preset atualizados nos Drafts pendentes; Product e legendas preservados');},'queue');};
document.addEventListener('click',event=>{
 const rename=event.target.closest('[data-batch-rename]');
 if(rename){const batch=state.batches.find(item=>item.id===Number(rename.dataset.batchRename));if(!batch)return;renamingBatchId=batch.id;renamingBatchValue=batch.name||'';renderQueue();const input=$('#queueBatches [data-batch-rename-input]');input?.focus();input?.select();return;}
 if(event.target.closest('[data-batch-rename-cancel]')){renamingBatchId=null;renamingBatchValue='';renderQueue();return;}
 const configure=event.target.closest('[data-batch-configure]');
 if(configure){configuringBatchId=Number(configure.dataset.batchConfigure);const form=$('#batchConfigurationForm');form.reset();fillSelect(field(form,'account_id'),state.accounts.filter(account=>account.active),'Manter conta atual');fillSelect(field(form,'preset_id'),state.presets,'Manter preset atual');$('#batchConfigurationDialog').showModal();return;}
 const cancel=event.target.closest('[data-batch-cancel]');
 if(cancel&&!cancel.disabled)task(async()=>{const id=Number(cancel.dataset.batchCancel),batch=state.batches.find(item=>item.id===id),count=batch?.queue_capabilities?.active_draft_count||0;if(!batch?.queue_capabilities?.can_cancel)throw Error('Este lote não pode ser cancelado com segurança');if(!confirm(`Cancelar este lote? ${count} Draft(s) sairão da fila. Mídias reservadas elegíveis serão liberadas; o histórico será preservado. Nenhum arquivo remoto será excluído.`))return;try{await api(`/batches/${id}/cancel`,{human_confirmed:true});}catch(error){let refreshed=false;try{await refresh();refreshed=true;}catch{}throw Error(refreshed?'O lote mudou de estado e não foi cancelado. A fila foi atualizada; revise as ações disponíveis.':'Não foi possível confirmar o cancelamento. Atualize a fila antes de tentar novamente.');}for(const draft of state.drafts.filter(item=>item.batch_id===id))selected.delete(draft.id);await refresh();notice(`${count} Draft(s) arquivados; histórico mantido e arquivos remotos preservados`);},'queue');
 if(event.target.closest('[data-queue-go-library]'))view('library');
});
$('#productSearch').oninput=()=>task(loadProductWorkspace);
$('#productScopes').onclick=e=>{const button=e.target.closest('[data-product-scope]');if(button){productScope=button.dataset.productScope;productSelected.clear();task(loadProductWorkspace);}};
$('#selectAll').onchange=e=>{visibleDrafts().forEach(d=>e.target.checked?selected.add(d.id):selected.delete(d.id));renderQueue();};
$('#clearQueueSelection').onclick=()=>{selected.clear();renderQueue();};
function edit(ids){if(!ids.length)throw Error('Selecione drafts');editing=ids;const form=$('#editForm');form.reset();$('#editTitle').textContent=`Editar ${ids.length} draft(s)`;if(ids.length===1){const d=state.drafts.find(d=>d.id===ids[0]);fillForm(form,d);field(form,'product_search').value=productLabel(state.products.find(p=>p.id===d.product_id));field(form,'change_caption').checked=true;field(form,'change_schedule').checked=true;field(form,'scheduled_at').value=d.scheduled_at?new Date(new Date(d.scheduled_at)-new Date(d.scheduled_at).getTimezoneOffset()*60000).toISOString().slice(0,16):'';}$('#editDialog').showModal();}
$('#bulkEdit').onclick=()=>task(()=>{if(!queueActionState('can_edit').ok)throw Error('A seleção contém item que não pode ser editado');edit([...selected]);});
$('#useProductCaption').onclick=()=>task(async()=>{if(!queueActionState('can_edit').ok)throw Error('A seleção contém item que não pode ser editado');await api('/bulk',{ids:[...selected],action:'use_product_caption'});await refresh();notice('Legenda do produto copiada para os drafts selecionados');});
$('#editForm').onsubmit=e=>{e.preventDefault();task(async()=>{const data=formData(e.target),fields={};if(data.product_search){const p=chosenProduct(data.product_search);if(!p)throw Error('Selecione um produto da lista');fields.product_id=p.id;}for(const k of ['account_id','preset_id'])if(data[k])fields[k]=Number(data[k]);if(data.change_caption)fields.caption=data.caption;if(data.change_schedule)fields.scheduled_at=data.scheduled_at?new Date(data.scheduled_at).toISOString():null;await api('/bulk',{ids:editing,fields});$('#editDialog').close();await refresh();notice('Drafts atualizados');});};
$('#archive').onclick=()=>task(async()=>{if(!queueActionState('can_remove').ok)throw Error('A seleção contém item que não pode ser removido');if(!confirm(`Remover ${selected.size} item(ns) da fila? A mídia poderá voltar a ficar disponível quando permitido.`))return;await api('/bulk',{ids:[...selected],action:'archive'});selected.clear();await refresh();notice('Itens removidos da fila; arquivos remotos preservados');});
$('#duplicate').onclick=()=>task(()=>{if(selected.size!==1)throw Error('Selecione um draft para copiar a configuração');const d=state.drafts.find(x=>selected.has(x.id));const form=$('#batchForm');form.reset();field(form,'name').value=`Cópia de ${d.filename}`;field(form,'account_id').value=String(d.account_id||'');field(form,'preset_id').value=String(d.preset_id||'');librarySelected.clear();renderBatchSummary();view('library');notice('Configuração copiada. Selecione novas mídias na Biblioteca.');});
$('#prepare').onclick=()=>task(async()=>{if($('#prepare').disabled)throw Error('A seleção contém item que precisa de correção');await api('/prepare',{ids:[...selected]});await refresh();notice('Preparação iniciada. Revisão e ação final permanecem pendentes.');});
document.addEventListener('click',e=>{const el=e.target.closest('[data-start-batch]');if(el&&!el.disabled)task(async()=>{const id=Number(el.dataset.startBatch),summary=await api(`/batches/${id}/preflight`);if(summary.blocked_items||!summary.total_items)throw Error(`${summary.blocked_items} item(ns) precisam de correção`);el.disabled=true;try{await api('/prepare',{batch_id:id});await refresh();notice('Lote enfileirado para processamento sequencial');}finally{el.disabled=false;}});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-remove-draft]');if(el&&!el.disabled)task(async()=>{const draft=state.drafts.find(d=>d.id===Number(el.dataset.removeDraft));if(!draft?.queue_capabilities?.can_remove)throw Error('Este item não pode ser removido');if(!confirm(`Remover ${draft.filename} (${draft.product_short_name||draft.product_name}) deste lote? A mídia poderá voltar a ficar disponível quando permitido.`))return;await api('/bulk',{ids:[draft.id],action:'archive'});selected.delete(draft.id);await refresh();});});
async function reorderQueue(group,ids){if(!ids||!group.all.every(d=>d.queue_capabilities?.can_reorder))throw Error('Este lote não pode ser reordenado');const container=$('#queueBatches');container.setAttribute('aria-busy','true');try{await api(`/batches/${group.batchId}/reorder`,{ordered_draft_ids:ids});await refresh();notice('Ordem salva');}catch(error){await refresh();throw error;}finally{container.removeAttribute('aria-busy');}}
document.addEventListener('click',e=>{const el=e.target.closest('[data-move]');if(el&&!el.disabled)task(async()=>{const id=Number(el.dataset.draft),group=queueGroups().find(g=>g.drafts.some(d=>d.id===id));if(!group)throw Error('Lote não encontrado');const index=group.all.findIndex(d=>d.id===id),target=group.all[index+(el.dataset.move==='up'?-1:1)];await reorderQueue(group,PublisherUIState.reorderedIds(group,id,target?.id));});});
let draggedQueueDraft=null;
$('#queueBatches').addEventListener('dragstart',e=>{const row=e.target.closest('[data-queue-draft]');draggedQueueDraft=row?.draggable?Number(row.dataset.queueDraft):null;if(draggedQueueDraft)e.dataTransfer.setData('text/plain',String(draggedQueueDraft));});
$('#queueBatches').addEventListener('dragover',e=>{if(draggedQueueDraft&&e.target.closest('[data-queue-draft]'))e.preventDefault();});
$('#queueBatches').addEventListener('drop',e=>{e.preventDefault();const target=Number(e.target.closest('[data-queue-draft]')?.dataset.queueDraft),source=draggedQueueDraft;draggedQueueDraft=null;if(!source||!target)return;task(async()=>{const group=queueGroups().find(g=>g.all.some(d=>d.id===source));await reorderQueue(group,PublisherUIState.reorderedIds(group,source,target));});});
$('#queueBatches').addEventListener('dragend',()=>{draggedQueueDraft=null;});
for(const [name,route] of [['product','products'],['account','accounts'],['preset','presets']])$('#'+name+'Form').onsubmit=e=>{e.preventDefault();task(async()=>{await api('/'+route,formData(e.target));e.target.reset();await refresh();notice('Cadastro salvo');if(route==='products'&&libraryBatchPanelState.resumeAfterFix)await returnToLibraryBatchPanel();});};
$('#newProduct').onclick=()=>{$('#productForm').reset();field($('#productForm'),'id').value='';$('#productLegacy').textContent='';renderProductEditor(null);field($('#productForm'),'name').focus();};
async function productBulk(action){if(!productSelected.size)throw Error('Selecione produtos');const plan=await api('/products/bulk',{action:'preview',ids:[...productSelected]});if(action==='delete'&&plan.will_archive)throw Error(`${plan.will_archive} produto(s) possuem histórico e não podem ser excluídos permanentemente. Você pode arquivá-los.`);const message=action==='delete'?`Excluir permanentemente ${plan.will_delete} produto(s) sem histórico? Esta ação não pode ser desfeita.`:action==='restore'?`Restaurar ${plan.selected} produto(s)?`:`Arquivar ${plan.selected} produto(s), mantendo todo o histórico?`;if(!confirm(message))return;const result=await api('/products/bulk',{action,ids:[...productSelected],human_confirmed:true});productSelected.clear();await refresh();notice(`${result.deleted||0} excluído(s); ${result.archived||0} arquivado(s); ${result.restored||0} restaurado(s)`);}
$('#archiveProducts').onclick=()=>task(()=>productBulk('archive'));$('#deleteProducts').onclick=()=>task(()=>productBulk('delete'));$('#restoreProducts').onclick=()=>task(()=>productBulk('restore'));$('#selectAllProducts').onchange=e=>{visibleProducts().forEach(p=>e.target.checked?productSelected.add(p.id):productSelected.delete(p.id));renderProductWorkspace();};
$('#addProductFolder').onclick=()=>task(async()=>{const productId=Number($('#productForm').dataset.productId),folderId=$('#productFolderSelect').value;if(!productId)throw Error('Salve o produto antes de associar uma pasta');if(!folderId)throw Error('Selecione uma pasta');await api('/product-folder',{product_id:productId,folder_id:folderId,action:'add'});await refresh();const product=state.products.find(p=>p.id===productId);if(product){fillForm($('#productForm'),product);renderProductEditor(product);}notice('Pasta associada; nenhum arquivo foi movido.');});
function visibleMedia(){const q=$('#mediaSearch').value.toLocaleLowerCase(),status=$('#mediaStatus').value;return (state.media_assets||[]).filter(m=>(!status||m.status===status)&&(!q||`${m.filename} ${m.folder_path} ${m.remote_file_id}`.toLocaleLowerCase().includes(q)));}
function renderMedia(){const items=visibleMedia();remoteSelected=new Set([...remoteSelected].filter(id=>(state.media_assets||[]).some(m=>m.id===id&&m.status==='AVAILABLE')));$('#fileCount').textContent=`(${remoteSelected.size} selecionados)`;$('#mediaCount').textContent=`${items.length} arquivos`;$('#selectAllMedia').checked=items.some(m=>m.status==='AVAILABLE')&&items.filter(m=>m.status==='AVAILABLE').every(m=>remoteSelected.has(m.id));$('#mediaRows').innerHTML=items.map(m=>`<tr><td><input type="checkbox" data-media="${m.id}" aria-label="Selecionar ${esc(m.filename)}" ${m.status!=='AVAILABLE'?'disabled':''} ${remoteSelected.has(m.id)?'checked':''}></td><td>${esc(m.filename)}<small>${esc(m.remote_file_id)}</small>${m.status!=='CONSUMED'?`<a href="https://drive.google.com/file/d/${encodeURIComponent(m.remote_file_id)}/view" target="_blank" rel="noopener">Ver no Drive ↗</a>`:''}</td><td>${esc(m.folder_path)}</td><td>${(m.size_bytes/1048576).toFixed(1)} MB<small>${esc(m.remote_modified_at||m.remote_created_at||'')}</small></td><td>${esc(m.status)}${m.cleanup_error?`<small>${esc(m.cleanup_error)}</small>`:''}${m.status==='CONSUMED_PENDING_DELETE'?`<button data-cleanup="${m.consumed_by_draft_id}">Repetir limpeza</button>`:''}</td></tr>`).join('')||'<tr><td colspan="5">Nenhum vídeo neste filtro.</td></tr>';}
if($('#mediaSearch'))$('#mediaSearch').oninput=renderMedia;if($('#mediaStatus'))$('#mediaStatus').onchange=renderMedia;
if($('#selectAllMedia'))$('#selectAllMedia').onchange=e=>{visibleMedia().filter(m=>m.status==='AVAILABLE').forEach(m=>e.target.checked?remoteSelected.add(m.id):remoteSelected.delete(m.id));renderMedia();};
const driveSyncPanel=document.createElement('div');
driveSyncPanel.className='drive-sync-panel';
driveSyncPanel.innerHTML='<div><strong id="driveSyncLabel"></strong><span id="driveSyncTime"></span><span id="driveSyncMode"></span><span id="driveSyncMessage" role="status" aria-live="polite"></span></div><label><input id="drivePollEnabled" type="checkbox"> Sincronização automática</label><label>Intervalo <select id="drivePollInterval"><option value="30">A cada 30 s</option><option value="60">A cada 1 min</option><option value="300">A cada 5 min</option><option value="900">A cada 15 min</option></select></label><span id="drivePollStatus" role="status"></span><small id="drivePollTimes"></small><button id="rebuildDriveSync" type="button">Verificação completa do Drive</button><details><summary>Detalhes técnicos</summary><small id="driveSyncDetails"></small></details>';
$('#syncMedia').closest('.heading').insertAdjacentElement('afterend',driveSyncPanel);
async function loadDriveSyncStatus(){
 const response=await fetch('/api/publisher/drive-sync/status',{cache:'no-store'});
 if(response.ok)driveSyncRecord=await response.json();
 else driveSyncRecord={cursor_status:'ERROR'};
}
function renderDriveSync(){
 const view=PublisherUIState.driveSyncPresentation(driveSyncRecord,driveSyncResult,driveSyncPhase);
 $('#driveSyncLabel').textContent=view.label;
 $('#driveSyncTime').textContent=driveSyncRecord.last_successful_sync_at?`Última sincronização: ${new Date(driveSyncRecord.last_successful_sync_at).toLocaleString('pt-BR')}`:'Nenhuma sincronização concluída';
 $('#driveSyncMode').textContent=driveSyncRecord.last_sync_mode?`Modo: ${driveSyncRecord.last_sync_mode==='FULL'?'Verificação completa':'Incremental'}`:'';
 $('#driveSyncMessage').textContent=view.message;
 const counts=driveSyncResult&&driveSyncResult.status==='READY'?['pages_processed','changes_seen','changes_in_scope','files_upserted','folders_upserted','items_marked_unavailable','projection_updates'].map(key=>`${key}: ${Number(driveSyncResult[key]||0)}`).join(' · '):'';
 $('#driveSyncDetails').textContent=`Estado: ${driveSyncRecord.cursor_status||'desconhecido'}${counts?` · ${counts}`:''}`;
 const poll=driveSyncRecord.polling||{};
 $('#drivePollEnabled').checked=!!poll.enabled;
 const intervalSelect=$('#drivePollInterval'), interval=String(poll.interval_seconds||60);
 intervalSelect.querySelector('option[data-custom]')?.remove();
 if(![...intervalSelect.options].some(option=>option.value===interval)){const custom=new Option(`A cada ${interval} s`,interval);custom.dataset.custom='true';intervalSelect.add(custom);}
 intervalSelect.value=interval;
 intervalSelect.disabled=!poll.enabled;
 $('#drivePollStatus').textContent=PublisherUIState.drivePollingPresentation(poll);
 const formatTime=value=>value?new Date(value).toLocaleString('pt-BR'):'';
 $('#drivePollTimes').textContent=poll.enabled?`Última: ${formatTime(poll.last_success_at)||'ainda não ocorreu'} · Próxima: ${formatTime(poll.next_attempt_at)||'aguardando ação'}`:'';
 $('#syncMedia').disabled=driveSyncBusy||view.needsRebuild||!!view.manualSyncBlocked;
 $('#syncMedia').textContent=driveSyncBusy?'Sincronizando…':'Atualizar biblioteca';
 $('#rebuildDriveSync').disabled=driveSyncBusy||!!view.manualSyncBlocked;
}
async function runDriveSync(bootstrap=false){
 if(driveSyncBusy)return;
 if(bootstrap&&!confirm('Verificar novamente toda a pasta operacional do Drive? Isso pode demorar. Nenhum arquivo remoto será excluído ou modificado.'))return;
 driveSyncBusy=true;driveSyncPhase='SYNCING';renderDriveSync();
 try{
  driveSyncResult=await api('/drive-sync/incremental',bootstrap?{bootstrap:true,human_confirmed:true}:{});
  driveSyncPhase=driveSyncResult.status==='READY'?'SUCCESS':driveSyncResult.status==='FULL_SCAN_REQUIRED'||driveSyncResult.status==='UNINITIALIZED'?'FULL_SCAN_REQUIRED':'ERROR';
  await loadDriveSyncStatus();
  if(driveSyncPhase==='SUCCESS'){
   await loadLibrary();renderLibrary();renderBatchSummary();
  }
 }catch(error){driveSyncPhase='ERROR';driveSyncResult={status:'ERROR'};await loadDriveSyncStatus();}
 finally{driveSyncBusy=false;renderDriveSync();}
}
$('#syncMedia').onclick=()=>runDriveSync();
$('#rebuildDriveSync').onclick=()=>runDriveSync(true);
async function changeDrivePolling(action){
 const control=$('#drivePollEnabled');control.disabled=true;
 try{await api('/drive-sync/polling',{action,interval_seconds:Number($('#drivePollInterval').value)});await loadDriveSyncStatus();renderDriveSync();}
 catch(error){await loadDriveSyncStatus();renderDriveSync();notice(error.message);}
 finally{control.disabled=false;}
}
$('#drivePollEnabled').onchange=e=>changeDrivePolling(e.target.checked?'enable':'disable');
$('#drivePollInterval').onchange=()=>changeDrivePolling('set_interval');
const legacyProductSearch=field($('#batchForm'),'product_search');if(legacyProductSearch)legacyProductSearch.onchange=e=>{const p=chosenProduct(e.target.value);renderCaptionPreview();if(p?.account_id)field($('#batchForm'),'account_id').value=p.account_id;};
field($('#batchForm'),'preset_id').onchange=e=>{const p=state.presets.find(x=>x.id===Number(e.target.value));if(p?.account_id)field($('#batchForm'),'account_id').value=p.account_id;};
$('#batchForm').onsubmit=e=>{e.preventDefault();task(async()=>{const p=chosenProduct(field(e.target,'product_search').value);if(!p)throw Error('Selecione um produto da lista');const data=formData(e.target);data.product_id=p.id;data.media_asset_ids=[...remoteSelected];const button=e.target.querySelector('button[type=submit]');button.disabled=true;try{const result=await api('/batches',data);e.target.reset();remoteSelected.clear();await refresh();selected=new Set(result.draft_ids);renderQueue();view('queue');notice(`${result.draft_ids.length} drafts criados sem baixar vídeos`);}finally{button.disabled=false;}});};
function patchActiveLibraryGroupHeader(currentRow,freshRow){
 const currentToggle=currentRow.querySelector('.library-group-toggle'),freshToggle=freshRow.querySelector('.library-group-toggle');
 if(currentToggle&&freshToggle){currentToggle.setAttribute('aria-expanded',freshToggle.getAttribute('aria-expanded'));currentToggle.innerHTML=freshToggle.innerHTML;}
 const currentCount=currentRow.querySelector('.library-group-header > span:not(.status)'),freshCount=freshRow.querySelector('.library-group-header > span:not(.status)');
 if(currentCount&&freshCount)currentCount.textContent=freshCount.textContent;
 const currentStatus=currentRow.querySelector('.library-group-header > span.status'),freshStatus=freshRow.querySelector('.library-group-header > span.status');
 if(currentStatus&&freshStatus){currentStatus.className=freshStatus.className;currentStatus.textContent=freshStatus.textContent;}
 const currentPath=currentRow.querySelector('.library-group-path'),freshPath=freshRow.querySelector('.library-group-path');
 if(currentPath&&freshPath){currentPath.textContent=freshPath.textContent;currentPath.title=freshPath.title;}
 const currentDetails=currentRow.querySelector('td > details'),freshDetails=freshRow.querySelector('td > details');
 if(currentDetails&&freshDetails)currentDetails.innerHTML=freshDetails.innerHTML;
}
function renderLibrary(){
 const library=state.library||{groups:[],total_groups:0,page:1,pages:0};
 const items=visibleLibraryItems();
 $('#libraryCount').textContent=`(${library.total_groups||0} grupos · ${library.total_matching_media||0} mídias)`;
 const hidden=Math.max(0,librarySelected.size-items.filter(item=>librarySelected.has(String(item.drive_file_id))).length);
 $('#librarySelectionCount').textContent=`${librarySelected.size} selecionados${hidden?` · ${hidden} fora da visualização atual`:''}`;
 $('#selectAllLibrary').checked=items.length>0&&items.every(item=>librarySelected.has(String(item.drive_file_id)));
 $('#libraryPrevious').disabled=(library.page||1)<=1;
 $('#libraryNext').disabled=(library.page||1)>=(library.pages||1);
 $('#libraryPageLabel').textContent=library.total_groups?`Página ${library.page} de ${library.pages} · grupos`:'Nenhum resultado';
 const signature=value=>encodeURIComponent(JSON.stringify(value));
 const mediaRow=item=>{
    const unavailable=item.status==='REMOTE_UNAVAILABLE';
    const thumbnail=item.file_type==='VIDEO'&&!unavailable?`<span class="thumbnail-wrap"><img class="media-thumbnail" data-thumbnail src="/api/publisher/library/thumbnail/${encodeURIComponent(item.drive_file_id)}" alt="Prévia de ${esc(item.filename)}" loading="lazy"><span class="media-thumbnail placeholder" hidden aria-hidden="true">▶</span></span>`:`<span class="media-thumbnail placeholder" aria-hidden="true">▶</span>`;
    const canDelete=!unavailable&&!item.media_asset_id&&item.status!=='CONSUMED'&&item.status!=='RESERVED';
    const menu=`<details class="row-menu"><summary aria-label="Mais ações para ${esc(item.filename)}">⋯</summary><div><button type="button" data-change-library-product="${esc(item.drive_file_id)}">Alterar somente este vídeo</button><button type="button" data-ignore-library="${esc(item.drive_file_id)}">Ignorar</button>${canDelete?`<button class="danger" type="button" data-delete-library="${esc(item.drive_file_id)}" data-delete-filename="${esc(item.filename)}" data-delete-size="${esc(item.size||0)}">Excluir do Drive</button>`:''}</div></details>`;
    const origin={MEDIA_OVERRIDE:'Override deste vídeo',DIRECT_FOLDER:'Produto da pasta',ANCESTOR_FOLDER:'Produto herdado da pasta'}[item.product_resolution]||'Produto da pasta não associado';
    const rowSignature=signature([item.drive_file_id,item.status,item.reason,item.product_id,item.product_archived,item.caption,item.classification_label,item.product_resolution,librarySelected.has(String(item.drive_file_id))]);
    return `<tr data-library-row-key="media:${esc(item.drive_file_id)}" data-render-signature="${rowSignature}"><td><input type="checkbox" data-library="${esc(item.drive_file_id)}" aria-label="Selecionar ${esc(item.filename)}" ${unavailable?'disabled':''} ${librarySelected.has(String(item.drive_file_id))?'checked':''}></td><td><div class="media-file">${thumbnail}<span><strong>${esc(item.filename)}</strong><small>${esc(item.mime_type||item.file_type)} · ${item.size?`${(Number(item.size)/1048576).toFixed(1)} MB`:''}</small></span></div></td><td>${esc(item.folder_path||item.folder_name||'—')}</td><td><strong>${esc(item.product_short_name||item.product_name||'Sem produto')}</strong>${item.product_short_name&&item.product_name&&item.product_short_name!==item.product_name?`<small title="${esc(item.product_name)}">${esc(item.product_name)}</small>`:''}<small>${esc(origin)} · ${item.product_archived?'Produto arquivado':item.product_name?(item.caption?.trim()?'Legenda do produto pronta':'Legenda não configurada'):'Associe produto à pasta'}</small></td><td><span title="${esc(item.classification_reason||'')}">${esc(item.classification_label||'Revisar')}</span></td><td><span class="status ${item.status==='AVAILABLE'?'ready':item.status==='ATTENTION'?'warning':item.status==='IGNORED'?'muted':''}">${esc(item.status_label||item.status)}</span><small>${esc(friendlyLibraryReason(item.reason))}</small></td><td class="library-row-actions">${!unavailable&&item.web_view_link?`<a href="${esc(item.web_view_link)}" target="_blank" rel="noopener">Ver no Drive ↗</a>`:''}${unavailable?'':menu}</td></tr>`;
 };
 const rowMarkup=(library.groups||[]).map(group=>{
    const expanded=expandedLibraryGroups.has(group.group_key),loaded=libraryGroupPages.get(group.group_key);
    const label=group.group_type==='PRODUCT'?group.display_name:`Sem produto — ${group.display_name}`;
    const count=group.matching_media_count===group.total_media_count?`${group.total_media_count} mídia(s)`:`${group.matching_media_count} de ${group.total_media_count} mídia(s) correspondem`;
    const folderId=group.folder_ids[0],pickerKey=group.group_type==='UNASSIGNED_FOLDER'?`folder:${folderId}`:'';
    const selectableCount=group.matching_available_count??group.available_count;
    const folderAction=group.group_type==='UNASSIGNED_FOLDER'?`<div class="product-combobox compact" data-folder-combobox="${esc(folderId)}" data-product-picker-key="${esc(pickerKey)}" data-folder-context="${esc(group.representative_folder_path)}"><label>Produto da pasta<input type="search" placeholder="Pesquisar produto..." autocomplete="off" role="combobox" aria-expanded="false"><input type="hidden" data-folder-product="${esc(folderId)}"><div class="combobox-results" role="listbox" hidden></div></label></div><button type="button" data-folder-assign="${esc(folderId)}">Associar produto à pasta</button>`:group.product_archived?`<span class="status warning">Produto arquivado</span><button type="button" data-library-restore-product="${esc(group.product_id)}">Restaurar produto</button>`:`<button type="button" data-library-config-product="${esc(group.product_id)}" data-product-archived="0">${group.has_default_caption?'Configurar produto':'Configurar legenda'}</button><button type="button" data-library-group-select="${esc(group.group_key)}" ${selectableCount?'':'disabled'}>Selecionar vídeos</button>`;
    const paths=group.folder_paths||[];
    const identity=JSON.stringify([group.group_type,group.product_id,group.folder_ids]);
    const stateLabel=group.presentation_state==='IGNORED'?'muted':group.presentation_state==='REQUIRES_ATTENTION'?'warning':'ready';
    const actions=group.presentation_state==='REMOTE_UNAVAILABLE'?'':`${folderAction}${group.ignoreable_count?`<button type="button" data-ignore-library-group="${esc(group.group_key)}" data-ignore-count="${group.ignoreable_count}">Ignorar ${group.ignoreable_count} ${group.ignoreable_count===1?'vídeo':'vídeos'} deste grupo</button>`:''}`;
    const headerSignature=signature([group.group_key,identity,group.display_name,count,group.folder_count,group.has_default_caption,group.presentation_state,group.display_status,group.review_reason,group.folder_paths,group.product_archived,selectableCount,group.ignoreable_count,expanded]);
    const header=`<tr class="library-group-row" data-library-row-key="group:${esc(group.group_key)}" data-library-group-row="${esc(group.group_key)}" data-group-identity="${esc(identity)}" data-render-signature="${headerSignature}"><td colspan="7"><div class="library-group-header"><button type="button" class="library-group-toggle" data-library-group-toggle="${esc(group.group_key)}" aria-expanded="${expanded}" aria-label="${expanded?'Recolher':'Expandir'} ${esc(label)}">${expanded?'▾':'▸'} <strong>${esc(label)}</strong></button><span>${esc(count)} · ${group.folder_count} pasta(s) · ${group.has_default_caption?'Legenda configurada':'Legenda não configurada'}</span><span class="status ${stateLabel}">${esc(group.display_status||group.review_reason||'Sem vídeos prontos')}</span><div class="library-group-actions">${actions}</div></div><small class="library-group-path" title="${esc(paths.join(' · '))}">${esc(group.representative_folder_path)}</small>${group.presentation_state==='IGNORED'?`<small>${esc(group.ignored_reason||'Grupo ignorado')}</small>`:''}${paths.length>1?`<details><summary>Ver ${paths.length} pastas</summary>${paths.map(path=>`<small>${esc(path)}</small>`).join('')}</details>`:''}</td></tr>`;
    const rows=[header];
    if(expanded){
      if(loaded)rows.push(...loaded.items.map(mediaRow));
      else rows.push(`<tr data-library-row-key="loading:${esc(group.group_key)}" data-render-signature="loading"><td colspan="7">Carregando vídeos…</td></tr>`);
      if(loaded&&(loaded.page||1)<(loaded.pages||0))rows.push(`<tr data-library-row-key="more:${esc(group.group_key)}:${loaded.page}" data-render-signature="more:${loaded.page}:${loaded.total}"><td colspan="7"><button type="button" data-library-group-more="${esc(group.group_key)}">Carregar mais vídeos (${loaded.items.length} de ${loaded.total})</button></td></tr>`);
    }
    return rows.join('');
 }).join('')||'<tr class="empty" data-library-row-key="empty" data-render-signature="empty"><td colspan="7">Nenhum grupo encontrado nesta combinação de filtros.</td></tr>';
 const template=document.createElement('template');template.innerHTML=rowMarkup;
 const desired=[...template.content.querySelectorAll('tr')],tbody=$('#libraryRows');
 let existing=new Map([...tbody.children].filter(row=>row.tagName==='TR').map(row=>[row.dataset.libraryRowKey,row]));
 let activeKey=activeProductPickerKey?.startsWith('folder:')?activeProductPickerKey:null;
 let activeHeader=activeKey?existing.get(`group:${activeKey}`):null;
 const freshActiveHeader=activeKey?desired.find(row=>row.dataset.libraryRowKey===`group:${activeKey}`):null;
 const identityValid=!!activeHeader&&!!freshActiveHeader&&activeHeader.dataset.groupIdentity===freshActiveHeader.dataset.groupIdentity;
 if(activeKey&&!identityValid){closeProductPicker(activeKey,{rerender:false});productPickerStates.delete(activeKey);productPickerComponents.delete(activeKey);activeKey=null;activeHeader=null;}
 const preserveActive=!!activeKey&&identityValid;
 const desiredNodes=[];
 for(const fresh of desired){
  const key=fresh.dataset.libraryRowKey,old=existing.get(key);
  if(preserveActive&&fresh===freshActiveHeader){
   activeHeader=old;
   if(old.dataset.renderSignature!==fresh.dataset.renderSignature){libraryRenderDeferred=true;patchActiveLibraryGroupHeader(old,fresh);}
   desiredNodes.push(old);existing.delete(key);continue;
  }
  if(old&&old.dataset.renderSignature===fresh.dataset.renderSignature){desiredNodes.push(old);existing.delete(key);continue;}
  if(old){old.replaceWith(fresh);existing.delete(key);}
  desiredNodes.push(fresh);
 }
 for(const stale of existing.values())stale.remove();
 if(preserveActive){
  const pivot=desiredNodes.indexOf(activeHeader);
  let anchor=activeHeader;
  for(let index=pivot-1;index>=0;index--){const row=desiredNodes[index];if(row.nextSibling!==anchor)tbody.insertBefore(row,anchor);anchor=row;}
  let after=activeHeader.nextSibling;
  for(let index=pivot+1;index<desiredNodes.length;index++){const row=desiredNodes[index];if(row!==after)tbody.insertBefore(row,after);after=row.nextSibling;}
 }else{
  let cursor=tbody.firstElementChild;
  for(const row of desiredNodes){if(row===cursor)cursor=cursor.nextElementSibling;else tbody.insertBefore(row,cursor);}
 }
 document.querySelectorAll('[data-folder-combobox]').forEach(container=>mountProductCombobox(container,{input:container.querySelector('input[type=search]'),hidden:container.querySelector('input[type=hidden]'),results:container.querySelector('.combobox-results'),context:container.dataset.folderContext,key:container.dataset.productPickerKey}));
 document.querySelectorAll('[data-thumbnail]').forEach(image=>{if(image.dataset.thumbnailBound)return;image.dataset.thumbnailBound='true';image.addEventListener('error',()=>{image.hidden=true;image.nextElementSibling.hidden=false;},{once:true});});
 refreshActiveProductPicker();
}
function renderBatchSummary(){
 const summary=uiState.librarySelectionSummary();
 const mappings=summary.details.map(item=>`${item.filename||item.drive_file_id} → ${item.product_name||'Sem produto'}`);
 const extra=Math.max(0,summary.selected-summary.known);
 $('#batchSelectionSummary').innerHTML=summary.selected?`<strong>${summary.selected} mídia(s) selecionada(s)</strong><span>${summary.productCount} produto(s) representado(s)</span><span>Prontas: ${summary.ready} · Sem produto: ${summary.withoutProduct} · Requer atenção: ${summary.attention}</span><span>${mappings.map(item=>esc(item)).join(' · ')}${extra?` · +${extra} em outras páginas`:''}</span><span>Conta padrão: ${esc(state.preferences?.default_publisher_account_name||'CECILIA FIGUEIREDO')}</span>`:'<span>Nenhuma mídia selecionada. Volte à Biblioteca para escolher os vídeos.</span>';
 const button=$('#createBatchFromLibrary');
 if(button){button.disabled=!summary.selected;button.textContent='Criar lote com selecionados';}
 const overrideButton=$('#assignLibraryProduct');if(overrideButton)overrideButton.disabled=summary.selected!==1;
}
function libraryBatchPayload(){return {drive_file_ids:[...librarySelected],name:field($('#libraryBatchForm'),'name').value,
 account_id:field($('#libraryBatchForm'),'account_id').value,preset_id:field($('#libraryBatchForm'),'preset_id').value};}
function renderLibraryBatchPreview(preview){
 const products=$('#libraryBatchProductSummary'),readiness=$('#libraryBatchReadiness');
 $('#libraryBatchSelectionText').textContent=`${preview.total} vídeo(s) selecionado(s) · ${Number(preview.product_count||0)} produto(s)`;
 products.innerHTML=(preview.products||[]).map(product=>`<span><strong>${esc(product.display_name)}</strong> — ${esc(product.count)} vídeo(s)</span>`).join('');
 const configuration=(preview.configuration_blockers||[]).map(blocker=>`<p>${esc(blocker.message)}</p>`).join('');
 const rows=(preview.items||[]).map(item=>`<article class="batch-preview-item"><div><strong>${esc(item.filename||item.drive_file_id)}</strong><small>${esc(item.product_display_name||'Sem produto')}</small></div><div><span class="${item.readiness==='READY'?'batch-ready':'status warning'}">${item.readiness==='READY'?'Pronto':'Requer correção'}</span>${(item.blockers||[]).map(blocker=>`<div class="batch-item-blocker">${esc(blocker.message)}${blocker.safe_fix_action?`<div class="batch-fix-actions"><button type="button" data-batch-fix="${esc(blocker.safe_fix_action.action)}" data-product-id="${esc(blocker.safe_fix_action.product_id??'')}" data-folder-id="${esc(blocker.safe_fix_action.folder_id??'')}">${esc(blocker.safe_fix_action.label)}</button></div>`:''}</div>`).join('')}</div><div>${(item.warnings||[]).map(warning=>`<p>${esc(warning.message||warning)}</p>`).join('')}</div></article>`).join('');
 const warnings=Number(preview.warning_count||0);
 readiness.innerHTML=`<div class="batch-readiness-overview"><span>${esc(preview.ready)} prontos</span><span>${esc(preview.blocked)} requerem correção</span><span>${warnings} aviso(s)</span></div>${configuration?`<div class="batch-config-blockers"><strong>Configuração do lote</strong>${configuration}</div>`:''}${rows?`<div class="batch-preview-items">${rows}</div>`:'<p>Nenhum item válido para prévia. Selecione vídeos explícitos na Biblioteca.</p>'}`;
 $('#libraryBatchSubmit').disabled=!preview.can_create||libraryBatchPanelState.submitting;
}
async function requestLibraryBatchPreview(){
 const dialog=$('#libraryBatchDialog');if(!dialog.open)return;
 const request=++libraryBatchPanelState.request,readiness=$('#libraryBatchReadiness');
 $('#libraryBatchSubmit').disabled=true;
 readiness.innerHTML='<p class="batch-panel-loading" role="status">Revalidando os itens selecionados…</p>';
 try{
  const preview=await api('/library-batches/preflight',libraryBatchPayload());
  if(request!==libraryBatchPanelState.request||!dialog.open)return;
  libraryBatchPanelState.preview=preview;renderLibraryBatchPreview(preview);
 }catch(error){
  if(request!==libraryBatchPanelState.request||!dialog.open)return;
  libraryBatchPanelState.preview=null;
  readiness.innerHTML=`<p class="status error">Não foi possível validar a seleção: ${esc(error.message)}</p>`;
  $('#libraryBatchSubmit').disabled=true;
 }
}
function scheduleLibraryBatchPreview(){
 libraryBatchPanelState.request++;
 clearTimeout(libraryBatchPreviewTimer);
 libraryBatchPreviewTimer=setTimeout(()=>task(requestLibraryBatchPreview,'library'),250);
}
function openLibraryBatchPanel(){
 if(!librarySelected.size){notice('Selecione mídias na Biblioteca antes de configurar o lote.',true,'library');return;}
 $('#libraryBatchSuccess').hidden=true;
 const account=field($('#libraryBatchForm'),'account_id');
 if(!account.value&&state.preferences?.default_publisher_account_id)account.value=String(state.preferences.default_publisher_account_id);
 libraryBatchPanelState.resumeAfterFix=false;
 $('#libraryBatchDialog').showModal();
 requestLibraryBatchPreview();
}
function closeLibraryBatchPanel(){
 libraryBatchPanelState.resumeAfterFix=false;
 libraryBatchPanelState.request++;
 if($('#libraryBatchDialog').open)$('#libraryBatchDialog').close();
}
function currentLibraryFilters(){return Object.fromEntries(['librarySearch','libraryPath','libraryProduct','libraryType','libraryGroupView','libraryStatus','libraryDecision'].map(id=>[id,$('#'+id).value]));}
async function returnToLibraryBatchPanel(){
 if(libraryBatchPanelState.filterSnapshot){
  for(const [id,value] of Object.entries(libraryBatchPanelState.filterSnapshot.values))$('#'+id).value=value;
  libraryPage=libraryBatchPanelState.filterSnapshot.page;
  libraryBatchPanelState.filterSnapshot=null;
  await loadLibrary();renderLibrary();renderBatchSummary();
 }
 libraryBatchPanelState.resumeAfterFix=false;
 view('library');
 if(!$('#libraryBatchDialog').open)$('#libraryBatchDialog').showModal();
 await requestLibraryBatchPreview();
}
async function associateFolderFromBatch(folderId){
 if(!folderId)throw Error('Esta mídia não informa uma pasta associável');
 if(!libraryBatchPanelState.filterSnapshot)libraryBatchPanelState.filterSnapshot={values:currentLibraryFilters(),page:libraryPage};
 libraryBatchPanelState.resumeAfterFix=true;
 $('#libraryBatchDialog').close();
 try{
  $('#librarySearch').value='';$('#libraryPath').value='';$('#libraryProduct').value='';$('#libraryType').value='VIDEO';
  $('#libraryStatus').value='';$('#libraryDecision').value='';$('#libraryGroupView').value='REQUIRES_ATTENTION';
  const params=new URLSearchParams({group_view:'REQUIRES_ATTENTION',type:'VIDEO',page:'1',page_size:'20'});
  const first=await fetch('/api/publisher/library/groups?'+params),firstBody=await first.json();
  if(!first.ok)throw Error(firstBody.error||'Não foi possível localizar a pasta na Biblioteca');
  let targetPage=null;
  for(let page=1;page<=(firstBody.pages||1);page++){
   let body=firstBody;
   if(page>1){const response=await fetch('/api/publisher/library/groups?'+new URLSearchParams({...Object.fromEntries(params),page:String(page)}));body=await response.json();if(!response.ok)throw Error(body.error||'Não foi possível localizar a pasta na Biblioteca');}
   if((body.groups||[]).some(group=>group.group_type==='UNASSIGNED_FOLDER'&&(group.folder_ids||[]).some(id=>String(id)===String(folderId)))){targetPage=page;break;}
  }
  if(targetPage===null)throw Error('A pasta não está visível em Requer atenção; revise os filtros da Biblioteca');
  libraryPage=targetPage;await loadLibrary();renderLibrary();renderBatchSummary();view('library');
  const picker=productPickerComponents.get(`folder:${folderId}`);
  if(!picker?.input?.isConnected)throw Error('O seletor da pasta não está disponível nesta página');
  picker.input.scrollIntoView({block:'center'});picker.input.focus();
  notice('Escolha um produto e confirme “Associar produto à pasta”. A seleção do lote será preservada.');
 }catch(error){await returnToLibraryBatchPanel();throw error;}
}
async function openProductCaptionFromBatch(productId){
 if(!productId)throw Error('Produto não identificado');
 libraryBatchPanelState.resumeAfterFix=true;$('#libraryBatchDialog').close();
 try{
  productScope='current';$('#productSearch').value='';await loadProductWorkspace();
  let product=visibleProducts().find(item=>Number(item.id)===Number(productId));
  if(!product){productScope='no_current';await loadProductWorkspace();product=visibleProducts().find(item=>Number(item.id)===Number(productId));}
  if(!product)throw Error('Produto não encontrado; a seleção do lote foi preservada');
  fillForm($('#productForm'),product);renderProductEditor(product);view('products');
  field($('#productForm'),'default_caption').focus();$('#productForm').scrollIntoView({block:'start'});
  notice('Configure a legenda padrão do produto e salve. A Biblioteca revalidará esta seleção; o texto será copiado ao Draft na criação.');
 }catch(error){await returnToLibraryBatchPanel();throw error;}
}
$('#libraryBatchDialog').addEventListener('close',()=>{libraryBatchPanelState.request++;});
$('#libraryBatchClose').onclick=closeLibraryBatchPanel;$('#libraryBatchCancel').onclick=closeLibraryBatchPanel;
$('#libraryBatchForm').addEventListener('input',event=>{if(event.target.name==='name')scheduleLibraryBatchPreview();});
$('#libraryBatchForm').addEventListener('change',event=>{
 if(event.target.name==='preset_id'){
  const preset=state.presets.find(item=>item.id===Number(event.target.value));
  if(preset?.account_id)field($('#libraryBatchForm'),'account_id').value=String(preset.account_id);
 }
 scheduleLibraryBatchPreview();
});
$('#libraryBatchForm').onsubmit=event=>{event.preventDefault();task(async()=>{
 const form=event.currentTarget;if(!form.reportValidity())return;
 if($('#libraryBatchSubmit').disabled||!libraryBatchPanelState.preview?.can_create)return;
 libraryBatchPanelState.submitting=true;$('#libraryBatchSubmit').disabled=true;
 let result;
 try{
  result=await api('/library-batches',libraryBatchPayload());
 }catch(error){
  libraryBatchPanelState.submitting=false;
  const rejected=error.message.includes('BATCH_PREFLIGHT_FAILED');
  const message=rejected?'A prontidão mudou; nenhuma criação parcial foi feita. Revise o resultado atualizado.':'Não foi possível confirmar o resultado da solicitação. Confira a Fila antes de tentar novamente.';
  $('#libraryBatchReadiness').insertAdjacentHTML('afterbegin',`<p class="status error">${esc(message)}</p>`);
  await requestLibraryBatchPreview();
  notice(rejected?'A prontidão mudou. Nenhum lote parcial foi criado; revise os bloqueios.':message,true,'library');
  return;
 }
 librarySelected.clear();libraryBatchPanelState.preview=null;libraryBatchPanelState.submitting=false;
 $('#libraryBatchDialog').close();form.reset();view('library');
 renderLibrary();renderBatchSummary();
 $('#libraryBatchSuccessCount').textContent=`${result.draft_ids.length} Draft(s) adicionados à Fila. Nenhum worker foi iniciado.`;
 $('#libraryBatchSuccess').hidden=false;
 try{await refresh();view('library');}
 catch(error){notice('Lote criado, mas a tela não pôde ser atualizada. Consulte a Fila antes de iniciar qualquer ação.',true,'library');}
 });};
$('#libraryBatchGoQueue').onclick=()=>view('queue');
$('#createBatchFromLibrary').onclick=openLibraryBatchPanel;
document.addEventListener('click',event=>{const button=event.target.closest('[data-batch-fix]');if(!button)return;task(async()=>{
 const action=button.dataset.batchFix,productId=Number(button.dataset.productId),folderId=button.dataset.folderId;
 if(action==='RESTORE_PRODUCT'){
  const product=state.products.find(item=>Number(item.id)===productId),name=product?.short_name||product?.name||`produto ${productId}`;
  if(!confirm(`Restaurar o produto “${name}”? Associações e histórico serão preservados.`))return;
  await api('/products/bulk',{action:'restore',ids:[productId],human_confirmed:true});
  await refresh();await requestLibraryBatchPreview();notice('Produto restaurado. A seleção foi revalidada; nenhum lote foi criado.');
 }else if(action==='ASSOCIATE_PRODUCT')await associateFolderFromBatch(folderId);
 else if(action==='CONFIGURE_CAPTION')await openProductCaptionFromBatch(productId);
 });});
async function reloadLibrary(){libraryPage=1;await loadLibrary();renderLibrary();renderBatchSummary();}
['#librarySearch','#libraryPath','#libraryProduct','#libraryType','#libraryGroupView','#libraryStatus','#libraryDecision'].forEach(selector=>{const el=$(selector);if(el){el.oninput=()=>task(reloadLibrary,'library');el.onchange=()=>task(reloadLibrary,'library');}});
function selectVisibleLibrary(checked){const items=visibleLibraryItems().filter(item=>item.status!=='REMOTE_UNAVAILABLE');if(checked&&new Set([...librarySelected,...items.map(item=>String(item.drive_file_id))]).size>100){notice('A seleção excederia 100 vídeos. Selecione menos itens.',true,'library');renderLibrary();return;}uiState.setLibraryPageSelection(items,checked);renderLibrary();renderBatchSummary();}
$('#selectAllLibrary').onchange=e=>selectVisibleLibrary(e.target.checked);
$('#selectLibraryPage').onclick=()=>selectVisibleLibrary(true);
$('#clearLibrarySelection').onclick=()=>{uiState.clearLibrarySelection();renderLibrary();renderBatchSummary();};
async function decideSelected(decision){if(!librarySelected.size)throw Error('Selecione mídias');for(const drive_file_id of librarySelected)await api('/media-decision',{drive_file_id,decision});await refresh();notice(decision==='VALID'?'Mídias marcadas como válidas':'Mídias ignoradas');}
$('#markLibraryValid').onclick=()=>task(()=>decideSelected('VALID'));
$('#markLibraryIgnored').onclick=()=>task(()=>decideSelected('IGNORED'));
$('#assignLibraryProduct').onclick=()=>task(async()=>{if(librarySelected.size!==1)throw Error('Selecione exatamente um vídeo para criar o override');const product_id=$('#libraryAssignProduct').value;if(!product_id)throw Error('Selecione um produto');await api('/media-product',{drive_file_ids:[...librarySelected],product_id:Number(product_id)});$('#libraryAssignProduct').value='';$('#libraryAssignProductSearch').value='';await refresh();notice('Override aplicado somente a este vídeo');});
$('#libraryPrevious').onclick=()=>task(async()=>{if(libraryPage<=1)return;libraryPage-=1;await loadLibrary();renderLibrary();renderBatchSummary();});
$('#libraryNext').onclick=()=>task(async()=>{if(libraryPage>=(state.library?.pages||1))return;libraryPage+=1;await loadLibrary();renderLibrary();renderBatchSummary();});
document.addEventListener('click',e=>{const button=e.target.closest('[data-library-group-toggle]');if(!button)return;task(async()=>{const key=button.dataset.libraryGroupToggle;if(expandedLibraryGroups.has(key)){if(key.startsWith('folder:'))closeProductPicker(key,{rerender:false});expandedLibraryGroups.delete(key);}else{expandedLibraryGroups.add(key);if(!libraryGroupPages.has(key))await loadLibraryGroup(key);}renderLibrary();renderBatchSummary();});});
document.addEventListener('click',e=>{const button=e.target.closest('[data-library-group-more]');if(!button)return;task(async()=>{const key=button.dataset.libraryGroupMore;await loadLibraryGroup(key,(libraryGroupPages.get(key)?.page||0)+1);renderLibrary();renderBatchSummary();});});
document.addEventListener('click',e=>{const button=e.target.closest('[data-library-group-select]');if(!button)return;task(async()=>{const filters=Object.fromEntries(libraryFilterParams());const result=await api('/library/group-select',{...filters,group_key:button.dataset.libraryGroupSelect,selected_drive_file_ids:[...librarySelected]});for(const item of result.items)uiState.setLibrarySelection(item.drive_file_id,true,item);renderLibrary();renderBatchSummary();notice(`${result.items.length} vídeo(s) prontos selecionados.`);});});
document.addEventListener('click',e=>{const button=e.target.closest('[data-library-config-product]');if(!button)return;task(async()=>{const id=Number(button.dataset.libraryConfigProduct);productScope=button.dataset.productArchived==='1'?'archived':'current';$('#productSearch').value='';await loadProductWorkspace();let product=visibleProducts().find(item=>item.id===id);if(!product&&productScope==='current'){productScope='no_current';await loadProductWorkspace();product=visibleProducts().find(item=>item.id===id);}if(!product)throw Error('Produto não encontrado na configuração');fillForm($('#productForm'),product);renderProductEditor(product);view('products');$('#productForm').scrollIntoView({block:'start'});});});
document.addEventListener('click',e=>{const button=e.target.closest('[data-library-restore-product]');if(!button)return;task(async()=>{const id=Number(button.dataset.libraryRestoreProduct);const group=(state.library?.groups||[]).find(item=>Number(item.product_id)===id);const name=group?.product_name||group?.display_name||`produto ${id}`;if(!confirm(`Restaurar o produto "${name}"? As associações e o histórico existentes serão preservados.`))return;await api('/products/bulk',{ids:[id],action:'restore',human_confirmed:true});await refresh();notice('Produto restaurado. A prontidão da Biblioteca foi recalculada; nenhum lote foi criado.');});});
document.addEventListener('click',e=>{const button=e.target.closest('[data-ignore-library-group]');if(!button)return;task(async()=>{const group_key=button.dataset.ignoreLibraryGroup;const preview=await api('/library/group-decision',{group_key,decision:'IGNORED',human_confirmed:false});const count=Number(preview.eligible_count||0),unit=count===1?'vídeo':'vídeos';if(!count)throw Error('Este grupo não tem mídias seguras para ignorar.');if(!confirm(`Ignorar ${count} ${unit} deste grupo? Mídias reservadas, em processamento, consumidas ou aguardando reconciliação não serão incluídas.`))return;const result=await api('/library/group-decision',{group_key,decision:'IGNORED',human_confirmed:true,expected_count:count});await refresh();const ignoredUnit=result.ignored_count===1?'vídeo':'vídeos';notice(`${result.ignored_count} ${ignoredUnit} ${result.ignored_count===1?'ignorado':'ignorados'} neste grupo.`);});});
$('#batchForm').onsubmit=e=>{e.preventDefault();task(async()=>{if(!librarySelected.size)throw Error('Selecione mídias na Biblioteca');const data=formData(e.target);data.drive_file_ids=[...librarySelected];const button=e.target.querySelector('button[type=submit]');button.disabled=true;try{const result=await api('/library-batches',data);librarySelected.clear();e.target.reset();await refresh();selected=new Set(result.draft_ids);renderQueue();view('queue');notice(`${result.draft_ids.length} drafts criados sem baixar vídeos`);}finally{button.disabled=false;}});};
function review(id,verificationOnly=false){reviewing=id;const d=state.drafts.find(x=>x.id===id);const evidence=state.logs.find(l=>l.draft_id===id&&l.screenshot_path);const ambiguous=PublisherUIState.ambiguousDraftPresentation(d)||(verificationOnly?{title:'Status a verificar',message:'Este vídeo pode já ter produzido efeito no TikTok. Confirme o estado antes de qualquer nova tentativa.',action:'Abrir Studio para verificar'}:null);$('#reviewBody').innerHTML=`<strong>${esc(d.filename)}</strong><div>Produto interno: ${esc(d.product_name)}<br>${esc(d.account_name)} · ${esc(d.queue_display?.label||d.status)}<br>Mídia: ${esc(d.media_status||'Legado local')}</div>${ambiguous?`<section class="safety-message"><strong>${esc(ambiguous.title)}</strong><p>${esc(ambiguous.message)}</p><button data-studio="${id}" ${state.worker_config?.tiktok_actions_paused?'disabled':''}>${esc(ambiguous.action)}</button></section>`:''}${d.remote_file_id&&d.media_status!=='CONSUMED'?`<a href="https://drive.google.com/file/d/${encodeURIComponent(d.remote_file_id)}/view" target="_blank" rel="noopener">Ver no Drive ↗</a>`:''}<pre>${esc(d.caption)}</pre><p>Horário desejado: ${d.scheduled_at?esc(new Date(d.scheduled_at).toLocaleString()):'Não definido'}</p><p>${Object.entries({upload:'Vídeo enviado',caption:'Legenda preenchida',settings:'Configurações preparadas',durable:'Draft persistido'}).map(([k,label])=>`${d.checklist[k]?'✓':'○'} ${label}`).join(' · ')}</p><p class="shopping-warning">Produto TikTok Shop ainda precisa ser vinculado manualmente.</p>${d.error_code?`<details><summary>Detalhes técnicos</summary><p>${esc(d.error_code)}</p></details>`:''}${ambiguous?'':`<div class="actions"><button data-studio="${id}" ${state.worker_config?.tiktok_actions_paused?'disabled':''}>Abrir / continuar no Studio</button><button data-complete="published">Registrar publicado</button><button data-complete="scheduled">Registrar agendado</button>${!d.confirmed_at?'<button data-complete="reset">Reabrir draft</button>':''}${d.media_status==='CONSUMED_PENDING_DELETE'?`<button data-cleanup="${id}">Repetir limpeza remota</button>`:''}</div>`}${evidence?`<a href="/api/publisher?evidence=${evidence.id}" target="_blank">Última evidência</a><img alt="Última tela do Studio" src="/api/publisher?evidence=${evidence.id}">`:''}`;$('#reviewDialog').showModal();}
document.addEventListener('change',e=>{if(e.target.dataset.media){const id=Number(e.target.dataset.media);e.target.checked?remoteSelected.add(id):remoteSelected.delete(id);renderMedia();}if(e.target.dataset.library){const item=visibleLibraryItems().find(row=>String(row.drive_file_id)===String(e.target.dataset.library));if(e.target.checked&&librarySelected.size>=100&&!librarySelected.has(String(e.target.dataset.library))){e.target.checked=false;notice('O lote aceita no máximo 100 vídeos.',true,'library');return;}uiState.setLibrarySelection(e.target.dataset.library,e.target.checked,item);renderLibrary();renderBatchSummary();}});
document.addEventListener('click',e=>{const el=e.target.closest('[data-delete-library]');if(el)task(async()=>{if(!confirm(`Excluir permanentemente "${el.dataset.deleteFilename}" do Drive? Esta ação não cria draft e não pode ser desfeita.`))return;el.disabled=true;try{await api('/media-delete',{drive_file_id:el.dataset.deleteLibrary,filename:el.dataset.deleteFilename,size:Number(el.dataset.deleteSize),human_confirmed:true});await refresh();notice('Arquivo excluído do Drive e removido da Biblioteca.');}finally{el.disabled=false;}});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-folder-assign]');if(el)task(async()=>{const selectedProduct=document.querySelector(`[data-folder-product="${CSS.escape(el.dataset.folderAssign)}"]`);if(!selectedProduct?.value)throw Error('Selecione um produto para a pasta');await api('/media-product',{scope:'folder',folder_id:el.dataset.folderAssign,product_id:Number(selectedProduct.value)});await refresh();notice('Produto associado à pasta. Arquivos herdáveis atuais e futuros usarão essa associação.');if(libraryBatchPanelState.resumeAfterFix)await returnToLibraryBatchPanel();});});
$('#refreshFolderCatalog').onclick=()=>task(loadFolderCatalog);
document.addEventListener('click',e=>{const el=e.target.closest('[data-catalog-group]');if(el){activeFolderCatalogGroup=el.dataset.catalogGroup;folderCatalogSelected.clear();renderFolderCatalog();}});
document.addEventListener('change',e=>{if(e.target.dataset.folderReviewSelect){const id=String(e.target.dataset.folderReviewSelect);e.target.checked?folderCatalogSelected.add(id):folderCatalogSelected.delete(id);renderFolderCatalog();}});
document.addEventListener('click',e=>{const el=e.target.closest('[data-bulk-folder-role]');if(el)task(async()=>{if(!folderCatalogSelected.size)throw Error('Selecione ao menos uma pasta deste grupo');const folder_ids=[...folderCatalogSelected];const role=el.dataset.bulkFolderRole;const preview=await api('/folder-catalog-bulk',{folder_ids,role,human_confirmed:false});const summary=`${preview.folders_affected} pasta(s) serão alteradas; ${preview.products_to_create} produto(s) serão criados; ${preview.existing_associations_preserved} associação(ões) existentes serão preservadas; ${preview.conflicts_excluded} conflito(s) foram excluídos.`;if(!preview.folders_affected)throw Error(summary);if(!confirm(`${summary}\n\nConfirmar esta decisão em massa?`))return;await api('/folder-catalog-bulk',{folder_ids,role,human_confirmed:true});await refresh();await loadFolderCatalog();notice('Decisão em massa aplicada após preview.');});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-folder-role]');if(el)task(async()=>{await api('/folder-catalog-decision',{folder_id:el.dataset.folderId,role:el.dataset.folderRole});await loadFolderCatalog();notice('Papel da pasta registrado como decisão humana.');});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-folder-link]');if(el)task(async()=>{await api('/folder-catalog-decision',{folder_id:el.dataset.folderLink,role:'PRODUCT',product_id:Number(el.dataset.productId)});await refresh();await loadFolderCatalog();notice('Pasta vinculada ao produto existente.');});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-folder-reject-match]');if(el)task(async()=>{await api('/folder-catalog-decision',{folder_id:el.dataset.folderRejectMatch,action:'reject_match'});await loadFolderCatalog();notice('Match rejeitado; a pasta voltou para revisão estrutural.');});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-folder-create]');if(el)task(async()=>{await api('/folder-catalog-decision',{folder_id:el.dataset.folderCreate,role:'PRODUCT',create_product:true});await refresh();await loadFolderCatalog();notice('Produto criado a partir da pasta.');});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-ignore-library]');if(el)task(async()=>{await api('/media-decision',{drive_file_id:el.dataset.ignoreLibrary,decision:'IGNORED',reason:'Ignorado na Biblioteca'});await refresh();notice('Mídia ignorada');});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-change-library-product]');if(el){librarySelected.clear();const item=visibleLibraryItems().find(row=>String(row.drive_file_id)===String(el.dataset.changeLibraryProduct));uiState.setLibrarySelection(el.dataset.changeLibraryProduct,true,item);renderLibrary();renderBatchSummary();$('#libraryAssignProductSearch').focus();notice('Escolha o produto para alterar somente este vídeo.');}});
document.addEventListener('click',e=>{const el=e.target.closest('[data-cleanup]');if(el)task(async()=>{
 if(state.worker_config?.persistence_workflow_version!==1)throw Error('Reinicie o Publisher para ativar o novo ciclo de consumo.');
 if(!confirm('Finalizar consumo: excluir somente o arquivo remoto deste draft e, depois, seu TEMP? O rascunho TikTok sera preservado.'))return;
 el.disabled=true;
 try{await api('/cleanup',{id:Number(el.dataset.cleanup),human_confirmed:true});notice('Limpeza enfileirada; nenhum upload sera repetido');}
 finally{el.disabled=false;}
});});
document.addEventListener('click',e=>{const el=e.target.closest('[data-review-protection]');if(el){const protection=(state.account_protection||[]).find(item=>item.account_id===Number(el.dataset.reviewProtection));const view=PublisherUIState.protectionPresentation(protection);if(!protection||!view)return;const dialog=$('#accountProtectionDialog');dialog.dataset.accountId=String(protection.account_id);$('#accountProtectionContext').innerHTML=`<dl class="auth-diagnostic-fields"><dt>Conta</dt><dd>${esc(protection.account_handle||protection.account_name)}</dd><dt>Estado</dt><dd>${esc(view.stateLabel)}</dd><dt>Motivo</dt><dd>${esc(view.reasonLabel)}</dd><dt>Detectado em</dt><dd>${esc(view.detectedAt)}</dd><dt>Evidência</dt><dd>${esc(view.evidence)}</dd></dl><details><summary>Detalhes técnicos</summary>${esc(protection.state)} · ${esc(protection.reason_code||'motivo não informado')} · ${esc(protection.source||'fonte não informada')}</details>`;$('#accountProtectionForm').reset();dialog.showModal();}});
$('#accountProtectionForm').onsubmit=e=>{e.preventDefault();task(async()=>{const data=formData(e.target);if(!data.human_confirmed)throw Error('Confirme a revisão humana');await api('/account-protection-clear',{account_id:Number($('#accountProtectionDialog').dataset.accountId),reason:data.reason,human_confirmed:true});$('#accountProtectionDialog').close();await refresh();notice('Proteção revisada e liberada.');});};
document.addEventListener('change',e=>{if(e.target.dataset.select){const id=Number(e.target.dataset.select);e.target.checked?selected.add(id):selected.delete(id);renderQueue();}if(e.target.dataset.productSelect){const id=Number(e.target.dataset.productSelect);e.target.checked?productSelected.add(id):productSelected.delete(id);renderProductWorkspace();}});
document.addEventListener('click',e=>{const el=e.target.closest('[data-remove-product-folder]');if(el)task(async()=>{const productId=Number($('#productForm').dataset.productId);if(!productId)throw Error('Produto não selecionado');if(!confirm('Remover somente a associação desta pasta? Nenhum arquivo será movido.'))return;await api('/product-folder',{product_id:productId,folder_id:el.dataset.removeProductFolder,action:'remove'});await refresh();const product=state.products.find(p=>p.id===productId);if(product){fillForm($('#productForm'),product);renderProductEditor(product);}notice('Associação removida e registrada no histórico.');});});
document.addEventListener('click',e=>task(async()=>{const el=e.target.closest('button');if(!el||el.disabled)return;const d=el.dataset;if(d.productEdit){const p=visibleProducts().find(x=>x.id===Number(d.productEdit));fillForm($('#productForm'),p);$('#productLegacy').innerHTML=`Fonte: ${esc(p.legacy_source||'Cadastro interno')}<br>ID legado: ${esc(p.legacy_product_id||'—')}<br>URL legada: ${esc(p.legacy_shop_url||'—')}`;renderProductEditor(p);}if(d.accountEdit)fillForm($('#accountForm'),state.accounts.find(x=>x.id===Number(d.accountEdit)));if(d.presetEdit)fillForm($('#presetForm'),state.presets.find(x=>x.id===Number(d.presetEdit)));if(d.login)await openAccountLogin(Number(d.login));if(d.edit)edit([Number(d.edit)]);if(d.review)review(Number(d.review),d.verification==='true');if(d.studio){await api('/review',{id:Number(d.studio)});notice('Abrindo a aba mantida pelo worker');}if(d.complete){const prompt=d.complete==='reset'?'Você encerrou ou descartou a preparação anterior no Studio?':'Você vinculou o produto TikTok Shop e concluiu esta ação manualmente no Studio?';if(!confirm(prompt))return;await api('/bulk',{ids:[reviewing],action:d.complete,human_confirmed:true});$('#reviewDialog').close();await refresh();notice('Estado registrado');}}));
task(refresh);loadDriveSyncStatus().then(renderDriveSync).catch(()=>{driveSyncRecord={cursor_status:'ERROR'};renderDriveSync();});setInterval(()=>task(refresh),5000);
setInterval(()=>loadDriveSyncStatus().then(renderDriveSync).catch(()=>{}),15000);
