/* Local headline configuration and explicit, local-only preview requests. */
const PublisherHeadlines = (() => {
 const defaults={font_ref:'',font_sha256:'',font_face_index:0,font_size_ratio:0.05,font_weight:400,text_color:'#FFFFFF',alignment:'center',x:0.5,y:0.15,margin_x:0.05,margin_y:0.05,max_width:0.9,max_lines:3,wrapping:'word',background_color:null,background_padding_ratio:0.01,stroke_color:null,stroke_width_ratio:0.002,shadow_color:null,shadow_offset_x:0.002,shadow_offset_y:0.002,shadow_blur_ratio:0.004};
 const numeric={font_face_index:['Face da fonte',0,255,1],font_size_ratio:['Tamanho relativo',0.005,0.3,0.001],font_weight:['Peso',100,900,100],x:['Posicao X',0,1,0.01],y:['Posicao Y',0,1,0.01],margin_x:['Margem horizontal',0,0.45,0.01],margin_y:['Margem vertical',0,0.45,0.01],max_width:['Largura maxima',0.01,1,0.01],max_lines:['Maximo de linhas',1,8,1],background_padding_ratio:['Espaco do fundo',0,0.1,0.001],stroke_width_ratio:['Espessura do contorno',0,0.05,0.001],shadow_offset_x:['Sombra X',-0.1,0.1,0.001],shadow_offset_y:['Sombra Y',-0.1,0.1,0.001],shadow_blur_ratio:['Desfoque da sombra',0,0.1,0.001]};
 let presets=[],templates=[],styles=[],signature='';
 const mediaChoices=new Map();let mediaSignature='';
 const sizeOptions='<option value="SMALL">Pequeno</option><option value="STANDARD" selected>Padrão</option><option value="LARGE">Grande</option>';
 function choices(select,keep=false){
  const value=select.value;
  const controlled=new Set(styles.filter(s=>s.size_key==='STANDARD').map(s=>s.preset_id));
  select.innerHTML=(keep?'<option value="">Manter snapshot atual</option>':'<option value="">Escolher estilo</option>')+presets.filter(p=>(!controlled.size||controlled.has(p.id)||String(p.id)===value)&&(!keep||p.active||String(p.id)===value)).map(p=>`<option value="${p.id}">${esc(styles.find(s=>s.preset_id===p.id)?.label||p.name)}${controlled.size?'':` / v${p.current_version}`}${p.active?'':' (arquivado)'}</option>`).join('');
  select.value=value;
 }
 function init(){
  const product=document.createElement('fieldset');product.id='productHeadline';
  product.innerHTML=`<legend>Headline no vídeo</legend><label class="check"><input type="checkbox" name="default_headline_enabled">Usar headline por padrão</label><label>Headline padrão<select name="default_headline_template_id"></select></label><label>Texto personalizado<textarea name="default_headline_text" rows="3"></textarea></label><div class="headline-choice-row"><label>Estilo padrão<select name="default_headline_preset_id"></select></label><label>Tamanho<select name="default_headline_size">${sizeOptions}</select></label></div><label class="check"><input type="checkbox" name="headline_required">Headline obrigatória</label><button type="button" data-new-product-headline>Criar nova headline</button>`;
  $('#productForm').insertBefore(product,$('#productForm').querySelector('[type=submit]'));
  const section=document.createElement('section');section.id='headlinePresets';
  section.innerHTML=`<h3>Presets visuais de headline</h3><div id="headlinePresetList" class="record-list"></div><form id="headlinePresetForm" class="editor"><input name="id" type="hidden"><input name="expected_version" type="hidden"><label>Nome<input name="name" maxlength="120" required></label><label class="check"><input name="active" type="checkbox" checked>Ativo</label><label>Referencia da fonte<input name="font_ref" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,127}" required></label><details><summary>Identidade da fonte</summary><label>SHA-256<input name="font_sha256" pattern="[0-9a-fA-F]{64}" required></label></details><div class="headline-fields">${Object.entries(numeric).map(([key,[label,min,max,step]])=>`<label>${label}<input name="${key}" type="number" min="${min}" max="${max}" step="${step}" value="${defaults[key]}" required></label>`).join('')}<label>Cor do texto<input type="color" name="text_color" value="#ffffff"></label><label>Alinhamento<select name="alignment"><option value="left">Esquerda</option><option value="center" selected>Centro</option><option value="right">Direita</option></select></label><label>Quebra de linha<select name="wrapping"><option value="word">Por palavra</option><option value="explicit">Somente explicita</option></select></label>${[['background','Fundo'],['stroke','Contorno'],['shadow','Sombra']].map(([key,label])=>`<label class="check"><input type="checkbox" name="${key}_enabled">${label}<input type="color" name="${key}_color" value="#000000" aria-label="Cor: ${label}"></label>`).join('')}</div><div class="actions"><button type="submit" class="primary">Salvar versao</button><button type="reset">Novo preset visual</button></div></form>`;
  $('#presets').append(section);
  const advanced=document.createElement('details');advanced.dataset.headlineAdvanced='true';
  advanced.innerHTML='<summary>Configuração avançada de presets visuais</summary>';section.before(advanced);advanced.append(section);
  initLibrary(advanced);
  product.querySelector('[data-new-product-headline]').onclick=()=>openTemplate(null,{product_id:Number($('#productForm').dataset.productId)||null,text:field($('#productForm'),'default_headline_text').value},id=>{field($('#productForm'),'default_headline_template_id').value=String(id);});
  const dialog=document.createElement('dialog');dialog.id='headlineDialog';
  dialog.innerHTML='<div class="heading"><h2>Headline deste draft</h2><button type="button" title="Fechar">\u00d7</button></div><div id="headlineDialogBody"></div>';
  dialog.querySelector('button').onclick=()=>dialog.close();document.body.append(dialog);
  const entry=document.createElement('div');entry.id='draftHeadlineEntry';$('#editDialog').append(entry);
  const form=$('#headlinePresetForm');
  form.addEventListener('reset',()=>{field(form,'id').value='';field(form,'expected_version').value='';});
  form.onsubmit=e=>{e.preventDefault();task(async()=>{
   const data=formData(form),spec={};
   Object.keys(defaults).forEach(key=>spec[key]=key in numeric?Number(data[key]):data[key]);
   ['background','stroke','shadow'].forEach(key=>{if(!data[key+'_enabled'])spec[key+'_color']=null;});
   const result=await api('/headline-presets',{id:data.id||null,expected_version:Number(data.expected_version),name:data.name,active:data.active,spec});
   await refresh();editPreset(result.id);notice('Versao visual salva. Headlines existentes preservadas.');
  });};
  section.addEventListener('click',e=>{const button=e.target.closest('[data-headline-preset]');if(button)editPreset(Number(button.dataset.headlinePreset));});
 }
 function editPreset(id){
  const p=presets.find(item=>item.id===id);if(!p)return;
  const data={...p,...p.spec,expected_version:p.current_version};
  ['background','stroke','shadow'].forEach(key=>{data[key+'_enabled']=p.spec[key+'_color']!==null;data[key+'_color']=p.spec[key+'_color']||'#000000';});
  fillForm($('#headlinePresetForm'),data);
 }
 function render(state){
  document.querySelectorAll('[data-queue-draft]').forEach(row=>{const draft=state.drafts.find(d=>d.id===Number(row.dataset.queueDraft));const details=row.querySelector('.queue-details');if(!draft||!details)return;let info=details.querySelector('.headline-summary');if(!info){info=document.createElement('div');info.className='headline-summary';details.append(info);}info.innerHTML=summary(draft);});
  presets=state.headline_presets||[];templates=state.headline_templates||[];styles=state.headline_styles||[];
  const next=JSON.stringify([presets,templates,styles]);if(signature===next)return;signature=next;
  choices($('#productHeadline [name=default_headline_preset_id]'));
  templateChoices(field($('#productForm'),'default_headline_template_id'),Number($('#productForm').dataset.productId)||null);
  renderTemplates();
  $('#headlinePresetList').innerHTML=presets.map(p=>`<div class="record"><div>${esc(p.name)}<small>v${p.current_version} / ${p.active?'Ativo':'Arquivado'}</small></div><button type="button" data-headline-preset="${p.id}">Editar</button></div>`).join('')||'<p>Nenhum preset visual.</p>';
 }
 function edit(ids){
  const entry=$('#draftHeadlineEntry');entry.replaceChildren();if(!ids.length)return;
  if(ids.length>1){renderBulkDrafts(ids,entry);return;}
  const draft=state.drafts.find(d=>d.id===ids[0]),h=draft.headline;
  entry.innerHTML=`<p>${h?.enabled?`Headline: ${esc(h.text)}`:'Sem headline'}</p><button type="button">Configurar headline deste draft</button>`;
  entry.querySelector('button').onclick=()=>{inspect(draft,$('#headlineDialogBody'));$('#headlineDialog').showModal();};
 }
 function inspect(draft,target=$('#reviewBody')){
  const h=draft.headline||{enabled:false,required:false,text:'',preset:null,provenance:'DISABLED'};
  const box=document.createElement('section');box.id='draftHeadline';
  const origin={PRODUCT_DEFAULT:'Padrao do produto',DRAFT_OVERRIDE:'Ajuste deste draft',DISABLED:'Desativada'};
  box.innerHTML=`<h3>Headline</h3><p>${h.enabled?esc(h.text)||'Texto pendente':'Sem headline'}</p><p>${h.preset?`${esc(h.preset.name)} / v${h.preset.version}`:'Sem preset visual'} / ${esc(origin[h.provenance]||h.provenance)}</p><details><summary>Contrato de render</summary><pre>${esc(JSON.stringify(h,null,2))}</pre></details>`;
  if(draft.queue_capabilities?.can_edit){
   box.insertAdjacentHTML('beforeend',`<form id="draftHeadlineForm" class="editor"><label class="check"><input name="enabled" type="checkbox" ${h.enabled||h.required?'checked':''} ${h.required?'disabled':''}>Ativada${h.required?' (obrigatoria)':''}</label><label>Texto deste draft<textarea name="text" rows="3">${esc(h.text)}</textarea></label><label>Preset visual<select name="headline_preset_id"></select></label><button type="submit">Salvar headline deste draft</button></form>`);
  }
  target.querySelector('#draftHeadline')?.remove();target.append(box);
  if(draft.queue_capabilities?.can_edit){
   const form=box.querySelector('#draftHeadlineForm');
   form.insertAdjacentHTML('afterbegin',`<label>Escolher headline<select name="mode">${headlineOptions(draft.product_id,true)}</select></label><div class="headline-choice-row"><label>Tamanho<select name="size">${sizeOptions}</select></label><button type="button" data-save-template>Criar nova headline</button></div>`);
   field(form,'mode').value='keep';field(form,'size').value=h.size||'STANDARD';
   form.querySelector('[data-save-template]').onclick=()=>openTemplate(null,{product_id:draft.product_id,text:field(form,'text').value},id=>{const mode=field(form,'mode');mode.insertAdjacentHTML('beforeend',`<option value="template:${id}">${esc(templates.find(t=>t.id===id)?.name||'Headline salva')}</option>`);mode.value=`template:${id}`;mode.dispatchEvent(new Event('change',{bubbles:true}));});
   field(form,'mode').onchange=()=>{const choice=field(form,'mode').value;if(choice.startsWith('template:'))field(form,'text').value=templates.find(t=>t.id===Number(choice.split(':')[1]))?.text||'';};
  }
  if(h.enabled&&draft.queue_capabilities?.can_edit){
   const button=document.createElement('button');button.type='button';button.textContent='Gerar pr\u00e9via';
   const preview=document.createElement('div');preview.className='headline-preview';preview.setAttribute('aria-live','polite');
   box.append(button,preview);
   let previewRevision=0;
   const invalidate=()=>{previewRevision++;button.disabled=true;preview.textContent='Prévia desatualizada';};
   box.querySelector('form').addEventListener('input',invalidate);
   box.querySelector('form').addEventListener('change',invalidate);
   button.onclick=async()=>{
    const requestedRevision=previewRevision;
    button.disabled=true;preview.textContent='Gerando pr\u00e9via local...';
    try{
     const result=await api(`/drafts/${draft.id}/headline-preview`,{});
     if(requestedRevision!==previewRevision)return;
     if(result.state!=='READY')throw new Error('Headline desativada.');
     const image=document.createElement('img');image.alt='Pr\u00e9via da headline deste draft';image.src=result.image;
     const stage=document.createElement('div');stage.className='headline-preview-stage';
     stage.append(image);const mask=document.createElement('div');mask.className='headline-platform-mask';mask.setAttribute('aria-hidden','true');mask.innerHTML='<span class="headline-safe-region"></span><span class="headline-action-region"></span><span class="headline-caption-region"></span>';stage.append(mask);
     const toggle=document.createElement('label');toggle.className='check';toggle.innerHTML='<input type="checkbox" checked>Mostrar área segura do TikTok';toggle.querySelector('input').onchange=e=>{mask.hidden=!e.target.checked;};preview.replaceChildren(toggle,stage);
    }catch(error){
     if(requestedRevision!==previewRevision)return;
     const message=String(error.message||error);
     preview.textContent=message.includes('NOT_CONFIGURED')?'Pr\u00e9via local ainda n\u00e3o habilitada neste ambiente.':message.includes('LOCAL_SOURCE')?'O v\u00eddeo precisa estar dispon\u00edvel localmente. Nenhum download foi iniciado.':'N\u00e3o foi poss\u00edvel gerar a pr\u00e9via. Confira fonte, texto e margens.';
    }finally{button.disabled=requestedRevision!==previewRevision;}
   };
  }
  const form=box.querySelector('#draftHeadlineForm');if(!form)return;
  choices(field(form,'headline_preset_id'),true);
  form.onsubmit=e=>{e.preventDefault();task(async()=>{
   const data=formData(form);if(h.required)data.enabled=true;
   if(data.mode==='keep'){delete data.mode;if(data.size===(h.size||'STANDARD')&&!data.headline_preset_id)delete data.size;}
   else{const mode=data.mode;if(mode.startsWith('template:')){data.mode='template';data.template_id=Number(mode.split(':')[1]);}delete data.enabled;}
   if(data.headline_preset_id)data.preset_id=data.headline_preset_id;
   delete data.headline_preset_id;
   await api(`/drafts/${draft.id}/headline`,data);await refresh();
   inspect(state.drafts.find(d=>d.id===draft.id),target);edit([draft.id]);notice('Headline local atualizada.');
  });};
 }
 function templateChoices(select,productId){
  const value=select.value;
  select.innerHTML='<option value="">Texto personalizado</option>'+templates.filter(t=>t.active&&(t.product_id===null||t.product_id===productId)).map(t=>`<option value="${t.id}">${esc(t.name)}${t.product_id===null?' · Global':''}</option>`).join('');select.value=value;
 }
 function productEdited(product){
  templateChoices(field($('#productForm'),'default_headline_template_id'),product?.id||null);
  field($('#productForm'),'default_headline_template_id').value=product?.default_headline_template_id||'';
  field($('#productForm'),'default_headline_size').value=product?.default_headline_size||'STANDARD';
 }
 function headlineOptions(productId,keep=false){
  return (keep?'<option value="keep">Manter snapshot atual</option>':'')+'<option value="none">Sem headline</option><option value="product_default" selected>Padrão do produto</option>'+['Global','Do produto'].map((label,index)=>`<optgroup label="${label}">${templates.filter(t=>t.active&&(index?t.product_id===productId:t.product_id===null)).map(t=>`<option value="template:${t.id}">${esc(t.name)}</option>`).join('')}</optgroup>`).join('')+'<option value="custom">Personalizada</option>';
 }
 let afterTemplateSave=null;
 function initLibrary(before){
  const section=document.createElement('section');section.id='headlineLibrary';
  section.innerHTML='<div class="heading"><h3>Headlines salvas</h3><button type="button" id="newHeadlineTemplate">Criar headline</button></div><div class="headline-choice-row"><label>Pesquisar<input id="headlineTemplateSearch" type="search"></label><label>Estado<select id="headlineTemplateFilter"><option value="active">Ativas</option><option value="inactive">Inativas</option><option value="all">Todas</option></select></label></div><div id="headlineTemplateList" class="record-list"></div><p id="headlineMachineStatus" role="status">Renderer: não verificado</p><div class="actions"><button type="button" id="checkHeadlineMachine">Verificar renderer</button><button type="button" id="initializeHeadlineStyles">Preparar estilos Classic</button></div>';
  before.before(section);
  const dialog=document.createElement('dialog');dialog.id='headlineTemplateDialog';
  dialog.innerHTML='<form id="headlineTemplateForm" class="editor"><div class="heading"><h2>Headline salva</h2><button type="button" title="Fechar">×</button></div><input name="id" type="hidden"><label>Nome<input name="name" maxlength="120" required></label><label>Texto<textarea name="text" rows="3" maxlength="500" required></textarea></label><label>Disponível para<select name="product_id"><option value="">Todos os produtos</option></select></label><label>Ordem<input name="sort_order" type="number" value="0" step="1"></label><label class="check"><input name="active" type="checkbox" checked>Ativa</label><button type="submit" class="primary">Salvar headline</button></form>';
  document.body.append(dialog);dialog.querySelector('[title=Fechar]').onclick=()=>dialog.close();
  $('#newHeadlineTemplate').onclick=()=>openTemplate();
  $('#headlineTemplateSearch').oninput=renderTemplates;$('#headlineTemplateFilter').onchange=renderTemplates;
  section.onclick=e=>{const button=e.target.closest('[data-template-edit],[data-template-deactivate]');if(!button)return;const id=Number(button.dataset.templateEdit||button.dataset.templateDeactivate),item=templates.find(t=>t.id===id);if(button.dataset.templateEdit)openTemplate(item);else task(async()=>{await api('/headline-templates',{...item,active:!item.active});await refresh();notice(item.active?'Headline desativada.':'Headline ativada.');});};
  $('#headlineTemplateForm').onsubmit=e=>{e.preventDefault();task(async()=>{const result=await api('/headline-templates',formData($('#headlineTemplateForm')));dialog.close();await refresh();afterTemplateSave?.(result.id);afterTemplateSave=null;notice('Headline salva.');});};
  $('#checkHeadlineMachine').onclick=()=>task(async()=>{const result=await api('/headline-workflow');$('#headlineMachineStatus').textContent=result.readiness.ready?'Renderer: Pronto':'Renderer: precisa de configuração';$('#headlineMachineStatus').dataset.ready=String(result.readiness.ready);$('#initializeHeadlineStyles').disabled=!result.readiness.ready;});
  $('#initializeHeadlineStyles').onclick=()=>task(async()=>{await api('/headline-styles/initialize',{});await refresh();notice('Estilos Classic preparados.');});
 }
 function openTemplate(item=null,initial={},callback=null){
  const form=$('#headlineTemplateForm'),select=field(form,'product_id');
  select.innerHTML='<option value="">Todos os produtos</option>'+(state.products||[]).filter(p=>p.active).map(p=>`<option value="${p.id}">${esc(p.short_name||p.name)}</option>`).join('');
  form.reset();fillForm(form,item||{id:'',...initial,active:true,sort_order:0});afterTemplateSave=callback;$('#headlineTemplateDialog').showModal();
 }
 function renderTemplates(){
  const query=$('#headlineTemplateSearch').value.toLocaleLowerCase(),filter=$('#headlineTemplateFilter').value;
  const rows=templates.filter(t=>(filter==='all'||!!t.active===(filter==='active'))&&(`${t.name} ${t.text}`).toLocaleLowerCase().includes(query));
  $('#headlineTemplateList').innerHTML=rows.map(t=>`<div class="record"><div><strong>${esc(t.name)}</strong><small>${esc(t.text)}</small><small>${t.product_id===null?'Global':esc(state.products.find(p=>p.id===t.product_id)?.short_name||state.products.find(p=>p.id===t.product_id)?.name||'Produto')} · ${t.active?'Ativa':'Inativa'}</small></div><div class="actions"><button type="button" data-template-edit="${t.id}">Editar</button><button type="button" data-template-deactivate="${t.id}">${t.active?'Desativar':'Ativar'}</button></div></div>`).join('')||'<p>Nenhuma headline neste filtro.</p>';
 }
 function selectionControls(productId,value={mode:'product_default'},prefix=''){
  const mode=value.mode==='template'?`template:${value.template_id}`:value.mode||'product_default';
  const styleOptions=styles.filter(s=>s.size_key==='STANDARD').map(s=>`<option value="${s.preset_id}">${esc(s.label)}</option>`).join('');
  return `<label>Headline<select data-headline-mode aria-label="Headline ${esc(prefix)}">${headlineOptions(productId)}</select></label><label data-headline-custom ${mode==='custom'?'':'hidden'}>Texto<textarea data-headline-text rows="2">${esc(value.text||'')}</textarea></label><div class="headline-choice-row"><label>Estilo<select data-headline-style><option value="">Padrão do produto</option>${styleOptions}</select></label><label>Tamanho<select data-headline-size>${sizeOptions}</select></label><button type="button" data-headline-new>Criar nova headline</button></div>`;
 }
 function readSelection(container){
  const raw=container.querySelector('[data-headline-mode]').value;
  const value={mode:raw.startsWith('template:')?'template':raw};
  if(value.mode==='template')value.template_id=Number(raw.split(':')[1]);
  if(value.mode==='custom')value.text=container.querySelector('[data-headline-text]').value;
  const preset=container.querySelector('[data-headline-style]').value;if(preset)value.preset_id=Number(preset);
  if(preset||value.mode!=='product_default'||container.querySelector('[data-headline-size]').dataset.explicit)value.size=container.querySelector('[data-headline-size]').value;
  return value;
 }
 function bindSelection(container,value,productId,onChange){
  const mode=container.querySelector('[data-headline-mode]');mode.value=value.mode==='template'?`template:${value.template_id}`:value.mode||'product_default';
  container.querySelector('[data-headline-style]').value=value.preset_id||'';container.querySelector('[data-headline-size]').value=value.size||'STANDARD';
  const update=e=>{if(e?.target?.matches('[data-headline-size]'))e.target.dataset.explicit='true';container.querySelector('[data-headline-custom]').hidden=mode.value!=='custom';onChange(readSelection(container));};
  container.onchange=update;container.oninput=e=>{if(e.target.matches('textarea'))onChange(readSelection(container));};
  container.querySelector('[data-headline-new]').onclick=()=>openTemplate(null,{product_id:productId,text:container.querySelector('[data-headline-text]').value},id=>{mode.insertAdjacentHTML('beforeend',`<option value="template:${id}">${esc(templates.find(t=>t.id===id)?.name||'Headline salva')}</option>`);mode.value=`template:${id}`;update();});
 }
 function renderSelection(items){
  let section=$('#libraryHeadlineSelection');if(!section){section=document.createElement('section');section.id='libraryHeadlineSelection';$('#libraryBatchReadiness').before(section);}
  const signature=JSON.stringify([items.map(i=>[i.drive_file_id,i.filename,i.product_id]),templates,styles]);
  if(mediaSignature===signature)return;mediaSignature=signature;
  for(const key of [...mediaChoices.keys()])if(!items.some(i=>i.drive_file_id===key))mediaChoices.delete(key);
  section.innerHTML=`<details><summary>Headline por vídeo (${items.length})</summary><div id="headlineMediaBulk"></div>${items.map(i=>`<div class="headline-media-row" data-headline-media="${esc(i.drive_file_id)}"><strong>${esc(i.filename||i.drive_file_id)}</strong><small>${esc(i.product_short_name||i.product_name||'Sem produto')}</small>${selectionControls(i.product_id,mediaChoices.get(i.drive_file_id),i.filename)}</div>`).join('')}</details>`;
  for(const item of items){const row=section.querySelector(`[data-headline-media="${CSS.escape(item.drive_file_id)}"]`);bindSelection(row,mediaChoices.get(item.drive_file_id)||{mode:'product_default'},item.product_id,value=>{mediaChoices.set(item.drive_file_id,value);scheduleLibraryBatchPreview();});}
  const bulk=$('#headlineMediaBulk');bulk.innerHTML=`<h4>Aplicar aos selecionados</h4>${selectionControls(null)}<div class="actions"><button type="button" data-bulk-text>Aplicar headline</button><button type="button" data-bulk-style>Aplicar estilo</button><button type="button" data-bulk-size>Aplicar tamanho</button></div>`;
  bindSelection(bulk,{mode:'product_default'},null,()=>{});
  for(const [selector,keys] of [['[data-bulk-text]',null],['[data-bulk-style]',['preset_id']],['[data-bulk-size]',['size']]])bulk.querySelector(selector).onclick=()=>{const patch=readSelection(bulk);if(keys?.includes('size'))patch.size=bulk.querySelector('[data-headline-size]').value;for(const item of items){const old=mediaChoices.get(item.drive_file_id)||{mode:'product_default'};mediaChoices.set(item.drive_file_id,keys?{...old,...Object.fromEntries(keys.filter(k=>patch[k]!==undefined).map(k=>[k,patch[k]]))}:patch);}mediaSignature='';renderSelection(items);scheduleLibraryBatchPreview();};
 }
 function selectionPayload(){return Object.fromEntries([...mediaChoices].filter(([id])=>librarySelected.has(id)));}
 function renderBulkDrafts(ids,entry){
  entry.innerHTML=`<h3>Headline dos selecionados</h3><div id="headlineDraftBulk">${selectionControls(null)}<div class="actions"><button type="button" data-bulk-text>Aplicar headline</button><button type="button" data-bulk-style>Aplicar estilo</button><button type="button" data-bulk-size>Aplicar tamanho</button></div></div>`;
  const box=entry.querySelector('#headlineDraftBulk'),frozen=ids.some(id=>!state.drafts.find(d=>d.id===id)?.queue_capabilities?.can_edit);
  bindSelection(box,{mode:'product_default'},null,()=>{});box.querySelectorAll('button,select,textarea').forEach(c=>c.disabled=frozen);
  if(frozen){const note=document.createElement('p');note.textContent='Há rascunhos congelados nesta seleção.';entry.append(note);}
  for(const [selector,keys] of [['[data-bulk-text]',null],['[data-bulk-style]',['preset_id']],['[data-bulk-size]',['size']]])box.querySelector(selector).onclick=()=>task(async()=>{const value=readSelection(box);if(keys?.includes('size'))value.size=box.querySelector('[data-headline-size]').value;const patch=keys?Object.fromEntries(keys.filter(k=>value[k]!==undefined).map(k=>[k,value[k]])):value;await api('/headlines/bulk',{ids,patch});await refresh();notice('Headlines dos rascunhos atualizadas.');});
 }
 function summary(draft){const h=draft.headline;return h?.enabled?`<p>Headline: ${esc(h.template?.name||h.text)}</p><small>Estilo: ${esc(h.preset?.name||'Não configurado')}</small>`:'<p>Sem headline</p>';}
 return {init,render,inspect,edit,renderSelection,selectionPayload,summary,productEdited};
})();
