/* Explicit local MP4 preparation, independent from the remote Queue. */
(() => {
 const endpoint='/local-preparations';
 const labels={QUEUED:'Pendente',PREPARING_SOURCE:'Preparando origem',VALIDATING_MEDIA:'Validando mídia',
  RENDERING_HEADLINE:'Renderizando headline',VALIDATING_VIDEO:'Validando vídeo',FINALIZING_LOCAL_FILE:'Finalizando arquivo local',
  READY:'Pronto',FAILED:'Falhou',CANCELLED:'Cancelado',REMOVED:'Arquivo local removido'};
 const button=document.createElement('button');
 button.id='prepareLocalFromLibrary';button.type='button';button.textContent='Preparar vídeo localmente';
 $('#createBatchFromLibrary').after(button);
 const panel=document.createElement('details');panel.id='localPreparationPanel';
 panel.innerHTML='<summary>Preparações locais — sem envio ao TikTok</summary><p>MP4 disponível por 7 dias. A remoção é local e explícita; não exclui a origem do Drive nem libera reservas de publicação.</p><p id="localPreparationStatus" role="status"></p><div id="localPreparationJobs"></div>';
 $('#librarySelectedPanel').after(panel);
 const dialog=document.createElement('dialog');dialog.id='localPreparationDialog';
 dialog.innerHTML='<div class="heading"><h2>Preparar vídeo localmente</h2><button type="button" data-local-close aria-label="Fechar preparação local">×</button></div><p><strong>Sem enviar ao TikTok.</strong> Cada vídeo começa sem headline. Escolha explicitamente onde aplicá-la.</p><details><summary>Configurar selecionados em conjunto</summary><div id="localPreparationBulk"></div><button type="button" id="localPreparationApplyBulk">Aplicar configuração aos vídeos desta seleção</button></details><div id="localPreparationItems"></div><button type="button" id="localPreparationReview">Revisar configurações</button><div id="localPreparationSummary" aria-live="polite"></div><p id="localPreparationError" role="alert"></p><button type="button" id="localPreparationStart" disabled>Iniciar somente preparação local</button>';
 document.body.append(dialog);
 const preview=document.createElement('dialog');preview.id='localPreparationVideo';
 preview.innerHTML='<button type="button">Fechar prévia</button><video controls preload="metadata" style="max-width:100%;max-height:75vh"></video>';
 document.body.append(preview);
 preview.querySelector('button').onclick=()=>preview.close();
 preview.addEventListener('close',()=>{const video=preview.querySelector('video');video.pause();video.removeAttribute('src');video.load();});
 dialog.querySelector('[data-local-close]').onclick=()=>dialog.close();
 let items=[],choices=new Map(),checked=null,requestId=null,busy=false,polling=false,jobs=[];
 function invalidate(){checked=null;$('#localPreparationStart').disabled=true;$('#localPreparationSummary').replaceChildren();$('#localPreparationError').textContent='';}
 function configure(container,productId,value,onChange){
  container.innerHTML=PublisherHeadlines.selectionControls(productId,value,'preparação local');
  PublisherHeadlines.bindSelection(container,value,productId,onChange);
 }
 function renderItems(){
  $('#localPreparationItems').innerHTML=items.map(item=>`<fieldset data-local-file="${esc(item.drive_file_id)}"><legend>${esc(item.filename)} · ${esc(item.drive_file_id)}</legend><div data-local-config></div></fieldset>`).join('');
  for(const item of items){
   const row=dialog.querySelector(`[data-local-file="${CSS.escape(item.drive_file_id)}"] [data-local-config]`);
   configure(row,item.product_id,choices.get(item.drive_file_id),value=>{choices.set(item.drive_file_id,value);invalidate();});
  }
 }
 button.onclick=()=>task(async()=>{
  if(!librarySelected.size)throw Error('Selecione uma ou mais mídias elegíveis na Biblioteca.');
  items=uiState.selectedLibraryDetails().filter(item=>librarySelected.has(String(item.drive_file_id))).map(item=>({...item,drive_file_id:String(item.drive_file_id)}));
  if(items.length!==librarySelected.size)throw Error('Atualize os detalhes das mídias selecionadas antes de preparar.');
  choices=new Map(items.map(item=>[item.drive_file_id,{mode:'none'}]));
  requestId=crypto.randomUUID().replaceAll('-','');invalidate();renderItems();
  configure($('#localPreparationBulk'),null,{mode:'none'},()=>{});
  dialog.showModal();
 },'library');
 $('#localPreparationApplyBulk').onclick=()=>{
  const value=PublisherHeadlines.readSelection($('#localPreparationBulk'));
  for(const item of items)choices.set(item.drive_file_id,{...value});
  invalidate();renderItems();
 };
 function payload(){return {items:items.map(item=>({drive_file_id:item.drive_file_id,headline:choices.get(item.drive_file_id)}))};}
 $('#localPreparationReview').onclick=()=>task(async()=>{
  checked=null;$('#localPreparationStart').disabled=true;
  $('#localPreparationError').textContent='';
  const captured=JSON.stringify(payload());let result;
  try{result=await api(endpoint+'/review',JSON.parse(captured));}
  catch(error){$('#localPreparationError').textContent=error.message;throw error;}
  if(captured!==JSON.stringify(payload()))return;
  checked=result;
  $('#localPreparationSummary').innerHTML='<h3>Revisão antes da preparação</h3>'+result.items.map(item=>{
   const h=item.snapshot;
   return `<p><strong>${esc(item.filename)}</strong> · ${esc(item.drive_file_id)}<br>${h.enabled?`Headline: ${esc(h.text)} · ${esc(h.preset.name)} · ${esc(h.size)}`:'Sem headline · original validado sem reencodificação'}</p>`;
  }).join('')+'<p>Nenhum upload, Save Draft, consumo ou exclusão no Drive será executado.</p>';
  $('#localPreparationStart').disabled=false;
 },'library');
 $('#localPreparationStart').onclick=()=>task(async()=>{
  if(!checked||busy)return;
  busy=true;$('#localPreparationStart').disabled=true;
  try{
   await api(endpoint,{...payload(),request_id:requestId,review_token:checked.review_token});
   dialog.close();panel.open=true;await poll();
  }catch(error){$('#localPreparationError').textContent=error.message;throw error;}
  finally{busy=false;$('#localPreparationStart').disabled=!checked;}
 },'library');
 function recovery(job){
  if(/HISTORY|RECONCILIATION|RESERVED/.test(job.error_code||''))return 'Revise a identidade e o histórico; não repita upload nem libere reservas.';
  if(/IDENTITY|CHANGED|CHECKSUM/.test(job.error_code||''))return 'Atualize o inventário e revise uma nova preparação; o snapshot anterior foi preservado.';
  return 'Corrija a origem ou a configuração local e solicite nova tentativa local. O snapshot permanece congelado.';
 }
 function renderJobs(){
  $('#localPreparationJobs').innerHTML=jobs.map(job=>{
   const m=job.metadata,last=job.events.at(-1),active=['QUEUED','PROCESSING'].includes(job.state);
   return `<article data-local-job="${job.id}"><h3>${esc(job.filename)} · ${esc(job.drive_file_id)}</h3><p>${esc(labels[job.state==='PROCESSING'?job.stage:job.state]||job.stage)} · ${job.headline_applied?'Com headline':'Sem headline'} · Nenhum envio ao TikTok</p><small>${esc(new Date(job.created_at).toLocaleString())}${m.duration?` · ${Number(m.duration).toFixed(2)}s · ${m.display.join('×')}`:''}${last?` · ${last.seconds}s decorridos`:''}</small>${job.error_code?`<p role="alert">${esc(job.error_code)} · Etapa: ${esc(labels[job.stage]||job.stage)}<br>${esc(recovery(job))}</p>`:''}${job.expired?'<p>Disponibilidade expirada. Remova o arquivo local ou prepare novamente.</p>':''}<div class="actions">${job.video_url?`<button type="button" data-local-preview="${job.id}">Abrir prévia</button><a href="${job.video_url}?download=1" download>Obter MP4 preparado</a>`:''}${active?`<button type="button" data-local-action="cancel">Cancelar preparação local</button>`:''}${job.can_retry?'<button type="button" data-local-action="retry">Tentar novamente localmente</button>':''}${!active&&job.state!=='REMOVED'?'<button type="button" data-local-action="remove">Remover arquivo local</button>':''}</div></article>`;
  }).join('')||'<p>Nenhuma preparação local registrada.</p>';
 }
 async function poll(){
  if(polling)return;polling=true;
  try{const result=await api(endpoint);jobs=result.jobs;renderJobs();$('#localPreparationStatus').textContent='';}
  catch(error){$('#localPreparationStatus').textContent=`Não foi possível consultar preparações locais: ${error.message}`;}
  finally{polling=false;}
 }
 panel.addEventListener('toggle',()=>{if(panel.open)poll();});
 panel.onclick=e=>task(async()=>{
  const previewButton=e.target.closest('[data-local-preview]');
  if(previewButton){const job=jobs.find(j=>j.id===previewButton.dataset.localPreview);preview.querySelector('video').src=job.video_url;preview.showModal();return;}
  const action=e.target.closest('[data-local-action]');if(!action||action.disabled)return;
  const id=action.closest('[data-local-job]').dataset.localJob;
  if(action.dataset.localAction==='remove'&&!confirm('Remover somente os arquivos desta preparação local? A origem no Drive e as reservas de publicação serão preservadas.'))return;
  action.disabled=true;
  try{await api(`${endpoint}/${id}/${action.dataset.localAction}`,{human_confirmed:true});await poll();}
  finally{action.disabled=false;}
 },'library');
 const timer=setInterval(()=>{if(panel.open&&!dialog.open&&!preview.open)poll();},1000);
 window.addEventListener('pagehide',()=>clearInterval(timer));
})();
