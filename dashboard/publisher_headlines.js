/* Local headline configuration and explicit, local-only preview requests. */
const PublisherHeadlines = (() => {
 const defaults={font_ref:'',font_sha256:'',font_face_index:0,font_size_ratio:0.05,font_weight:400,text_color:'#FFFFFF',alignment:'center',x:0.5,y:0.15,margin_x:0.05,margin_y:0.05,max_width:0.9,max_lines:3,wrapping:'word',background_color:null,background_padding_ratio:0.01,stroke_color:null,stroke_width_ratio:0.002,shadow_color:null,shadow_offset_x:0.002,shadow_offset_y:0.002,shadow_blur_ratio:0.004};
 const numeric={font_face_index:['Face da fonte',0,255,1],font_size_ratio:['Tamanho relativo',0.005,0.3,0.001],font_weight:['Peso',100,900,100],x:['Posicao X',0,1,0.01],y:['Posicao Y',0,1,0.01],margin_x:['Margem horizontal',0,0.45,0.01],margin_y:['Margem vertical',0,0.45,0.01],max_width:['Largura maxima',0.01,1,0.01],max_lines:['Maximo de linhas',1,8,1],background_padding_ratio:['Espaco do fundo',0,0.1,0.001],stroke_width_ratio:['Espessura do contorno',0,0.05,0.001],shadow_offset_x:['Sombra X',-0.1,0.1,0.001],shadow_offset_y:['Sombra Y',-0.1,0.1,0.001],shadow_blur_ratio:['Desfoque da sombra',0,0.1,0.001]};
 let presets=[],templates=[],styles=[],signature='',catalogStatus='LOADING';
 const swatches=new Map();
 const mediaChoices=new Map();let mediaSignature='';
 function previewFailure(error){
  const code=String(error?.message||error);
  if(/LOCAL_SOURCE/.test(code))return {kind:'SOURCE',message:'O vídeo ainda não está disponível localmente. Nenhum download foi iniciado. A prévia do vídeo é independente da amostra de estilo.'};
  if(/FONT|GLYPH|SHAPING/.test(code))return {kind:'FONT',message:'A fonte aprovada ou um caractere não está disponível para esta headline. Revise o texto ou a configuração da fonte.'};
  if(/FFMPEG|FFPROBE|PROCESS_UNAVAILABLE|TOOL_IDENTITY/.test(code))return {kind:'TOOL',message:'O renderer local precisa de verificação. Confira FFmpeg, ffprobe e a configuração no diagnóstico de headlines.'};
  if(/LAYOUT|MARGIN|LINES|BLOCK_HEIGHT|CONFIG|PRESET|TEXT|REQUIRED/.test(code))return {kind:'CONFIG',message:'A configuração da headline precisa de revisão. Edite o texto ou escolha outro estilo e tamanho.'};
  if(/CACHE|ARTIFACT|OUTPUT_INVALID|PREVIEW_TOO_LARGE/.test(code))return {kind:'ARTIFACT',message:'A prévia local não pôde ser validada. Tente gerá-la novamente; nenhum envio foi iniciado.'};
  if(/RENDERER/.test(code))return {kind:'TOOL',message:'O renderer de headline não está configurado ou está desativado neste ambiente.'};
  if(/DISABLED/.test(code))return {kind:'DISABLED',message:'Escolha uma headline para gerar a prévia.'};
  if(/PROCESS_EXIT_ERROR|RENDER_FAILED/.test(code))return {kind:'RENDER',message:'O processo local de renderização falhou. Consulte o diagnóstico do renderer ou tente a prévia novamente; nenhum envio foi iniciado.'};
  if(/PROCESS_TIMEOUT/.test(code))return {kind:'TEMPORARY',message:'O tempo disponível para a prévia local terminou. Tente novamente ou consulte o diagnóstico. O render final permanece pendente.'};
  if(/PATH|MEDIA|SOURCE_CHANGED|TIMING|CADENCE/.test(code))return {kind:'MEDIA',message:'A mídia local não passou na validação de leitura ou preparação. Revise o diagnóstico antes de enviar.'};
  return {kind:'TEMPORARY',message:'Não foi possível concluir a prévia local. Tente novamente ou consulte o diagnóstico do renderer. O render final ainda não foi validado.'};
 }
 function showPreviewFailure(preview,error,container){
  const failure=previewFailure(error),message=document.createElement('p');message.textContent=failure.message;
  preview.dataset.previewState='FAILED';preview.dataset.previewFailure=failure.kind;preview.replaceChildren(message);
  for(const [label,selector] of [['Editar headline','[data-headline-text],textarea'],['Alterar estilo','[data-headline-style],[data-comparison-style],select[name*=preset]']]){
   const control=container.querySelector(selector);if(!control||control.disabled)continue;
   const action=document.createElement('button');action.type='button';action.textContent=label;action.onclick=()=>{
    const mode=container.querySelector('[data-headline-mode]');
    if(mode&&control.matches('textarea')&&mode.value!=='custom'){
     const item=state.products.find(p=>p.id===Number(container.dataset.headlineProductId));
     if(mode.value.startsWith('template:'))control.value=templates.find(t=>t.id===Number(mode.value.split(':')[1]))?.text||'';
     else if(mode.value==='product_default')control.value=templates.find(t=>t.id===item?.default_headline_template_id)?.text||item?.default_headline_text||'';
     mode.value='custom';mode.dispatchEvent(new Event('change',{bubbles:true}));
    }
    control.closest('[hidden]')?.removeAttribute('hidden');control.focus();
   };preview.append(action);
  }
 }
 defaults.background_radius_ratio=0;
 numeric.background_radius_ratio=['Arredondamento do fundo',0,0.05,0.001];
 const sizeOptions='<option value="SMALL">Pequeno</option><option value="STANDARD" selected>Padrão</option><option value="LARGE">Grande</option>';
 function selectionStyleOptions(){
  return styles.filter(s=>s.size_key==='STANDARD'&&presets.some(p=>p.id===s.preset_id&&p.active));
 }
 function selectionStyleMarkup(value=''){
  const label=catalogStatus==='LOADING'?'Carregando estilos...':catalogStatus==='READY'?'Padrão do produto':'Estilos indisponíveis. Aguarde uma nova atualização.';
  const options=catalogStatus==='READY'?selectionStyleOptions():[];
  const missing=value&&!options.some(s=>String(s.preset_id)===String(value));
  return `<option value="">${label}</option>`+options.map(s=>`<option value="${s.preset_id}">${esc(s.label)}</option>`).join('')+(missing?`<option value="${esc(value)}" disabled>Estilo selecionado indisponível (ID ${esc(value)})</option>`:'');
 }
 function hydrateSelectionStyle(select){
  // Keep the input subtree intact while a late catalog supplies only its options.
  const value=select.value,markup=selectionStyleMarkup(value);
  const next=JSON.stringify([catalogStatus,markup,presets.map(p=>[p.id,p.current_version])]);
  const container=select.closest('#headlineMediaBulk,#headlineDraftBulk,.headline-media-row');
  const frozen=container?.dataset.headlineFrozen==='true';
  const valid=!value||selectionStyleOptions().some(s=>String(s.preset_id)===value);
  select.dataset.catalogState=catalogStatus;
  select.disabled=catalogStatus!=='READY'||frozen;
  select.setAttribute('aria-busy',String(catalogStatus==='LOADING'));
  select.setCustomValidity(catalogStatus!=='READY'?'Aguarde os estilos de headline.':valid?'':'O estilo selecionado não está disponível. Escolha outro estilo.');
  if(select.dataset.styleOptionsSignature!==next){
   select.innerHTML=markup;select.value=value;
   select.dataset.styleOptionsSignature=next;
   visualChoices(select);
  }
  container?.querySelectorAll('[data-bulk-text],[data-bulk-style]').forEach(button=>button.disabled=frozen||catalogStatus!=='READY'||!valid);
 }
 function hydrateOpenSelectionPanels(){
  document.querySelectorAll('#libraryBatchDialog[open] #libraryHeadlineSelection [data-headline-style],#editDialog[open] #headlineDraftBulk [data-headline-style]').forEach(hydrateSelectionStyle);
 }
 function catalogFailed(){
  if(catalogStatus==='READY')return;
  catalogStatus='ERROR';hydrateOpenSelectionPanels();
 }
 function choices(select,keep=false){
  const value=select.value;
  const controlled=new Set(styles.filter(s=>s.size_key==='STANDARD').map(s=>s.preset_id));
  select.innerHTML=(keep?'<option value="">Manter snapshot atual</option>':'<option value="">Escolher estilo</option>')+presets.filter(p=>(!controlled.size||controlled.has(p.id)||String(p.id)===value)&&(!keep||p.active||String(p.id)===value)).map(p=>`<option value="${p.id}">${esc(styles.find(s=>s.preset_id===p.id)?.label||p.name)}${controlled.size?'':` / v${p.current_version}`}${p.active?'':' (arquivado)'}</option>`).join('');
  select.value=value;
  visualChoices(select);
 }
 function visualChoices(select){
  let gallery=select.parentElement.nextElementSibling;
  if(!gallery?.matches('.headline-style-samples')){gallery=document.createElement('div');gallery.className='headline-style-samples';select.parentElement.after(gallery);}
  gallery.replaceChildren();
  for(const option of [...select.options].filter(o=>o.value)){
   const preset=presets.find(p=>String(p.id)===option.value);if(!preset)continue;
   const button=document.createElement('button');button.type='button';button.className='headline-style-sample';button.dataset.presetId=String(preset.id);button.title=`${option.textContent} / v${preset.current_version}`;
   button.innerHTML=`<img alt="" width="160" height="82"><span>${esc(option.textContent)}</span><small>v${preset.current_version}</small>`;
   button.onclick=()=>{if(select.disabled)return;select.value=String(preset.id);select.dispatchEvent(new Event('change',{bubbles:true}));};gallery.append(button);
   const key=`${preset.id}:${preset.current_version}`;
   const load=()=>{
    if(!swatches.has(key))swatches.set(key,api(`/headline-presets/${preset.id}/swatch?version=${preset.current_version}`));
    swatches.get(key).then(result=>{if(!button.isConnected)return;if(!result?.image||result.preset_version!==preset.current_version)throw Error('HEADLINE_CACHE_INVALID');button.querySelector('img').src=result.image;button.querySelector('img').hidden=false;button.querySelector('.headline-sample-fallback')?.remove();button.querySelector('small').textContent=`v${preset.current_version} · Amostra do estilo`;}).catch(error=>{
     if(!button.isConnected)return;button.querySelector('img').hidden=true;
     if(!button.querySelector('.headline-sample-fallback')){const sample=document.createElement('span');sample.className='headline-sample-fallback';sample.textContent='Classic';const spec=preset.spec||{};sample.style.color=spec.text_color||'#FFFFFF';sample.style.backgroundColor=spec.background_color||'#454545';if(spec.stroke_color)sample.style.webkitTextStroke=`1px ${spec.stroke_color}`;button.prepend(sample);}
     button.querySelector('small').textContent=`v${preset.current_version} · Ilustração simplificada`;button.title=previewFailure(error).message+' Esta ilustração não valida a fonte nem o render.';
     if(!gallery.querySelector(`[data-swatch-retry="${preset.id}"]`)){const retry=document.createElement('button');retry.type='button';retry.dataset.swatchRetry=String(preset.id);retry.textContent='Tentar amostra novamente';retry.onclick=()=>{retry.remove();swatches.delete(key);load();};button.after(retry);}
    });
   };
   const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){observer.disconnect();load();}});observer.observe(button);
  }
  const update=()=>gallery.querySelectorAll('[data-preset-id]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.presetId===select.value));b.disabled=select.disabled;});
  if(select._sampleUpdate)select.removeEventListener('change',select._sampleUpdate);select._sampleUpdate=update;select.addEventListener('change',update);update();
 }
 function previewImage(preview,result){
  const image=document.createElement('img');image.alt='Prévia da headline no vídeo';image.src=result.image;
  const stage=document.createElement('div');stage.className='headline-preview-stage';stage.append(image);
  const mask=document.createElement('div');mask.className='headline-platform-mask';mask.setAttribute('aria-hidden','true');mask.innerHTML='<span class="headline-safe-region"></span><span class="headline-action-region"></span><span class="headline-caption-region"></span>';stage.append(mask);
  const title=document.createElement('p');title.textContent=`${result.preset_name} / v${result.preset_version}`;
  const toggle=document.createElement('label');toggle.className='check';toggle.innerHTML='<input type="checkbox" checked>Mostrar área segura do TikTok';toggle.querySelector('input').onchange=e=>{mask.hidden=!e.target.checked;};preview.replaceChildren(title,toggle,stage);
 }
 function comparisonControl(container,request,buttonName='Gerar prévia'){
  const button=document.createElement('button');button.type='button';button.textContent=buttonName;button.dataset.stylePreview='true';
  const preview=document.createElement('div');preview.className='headline-style-preview';preview.setAttribute('aria-live','polite');container.append(button,preview);
  let revision=0,busy=false;
  const invalidate=e=>{if(preview.contains(e.target))return;revision++;preview.dataset.previewState='STALE';preview.textContent='Prévia desatualizada. Render final pendente.';};
  container.addEventListener('input',invalidate);
  container.addEventListener('change',invalidate);
  button.onclick=async()=>{if(busy)return;const current=revision;busy=true;button.disabled=true;preview.textContent='Gerando prévia local...';
   try{const result=await api('/headline-preview/compare',request());if(current!==revision)return;if(result.state!=='READY')throw new Error('HEADLINE_DISABLED');preview.dataset.previewState='READY';previewImage(preview,result);}
   catch(error){if(current!==revision)return;showPreviewFailure(preview,error,container);button.textContent='Tentar prévia novamente';}
   finally{busy=false;button.disabled=false;}
  };
 }
 function init(){
  const product=document.createElement('fieldset');product.id='productHeadline';
  product.innerHTML=`<legend>Sugestão de headline</legend><label class="check"><input type="checkbox" name="default_headline_enabled">Sugestão disponível</label><label>Headline sugerida<select name="default_headline_template_id"></select></label><label>Texto personalizado<textarea name="default_headline_text" rows="3"></textarea></label><div class="headline-choice-row"><label>Estilo sugerido<select name="default_headline_preset_id"></select></label><label>Tamanho<select name="default_headline_size">${sizeOptions}</select></label></div><input type="checkbox" name="headline_required" hidden><button type="button" data-new-product-headline>Criar nova headline</button>`;
  $('#productForm').insertBefore(product,$('#productForm').querySelector('[type=submit]'));
  comparisonControl(product,()=>{const form=$('#productForm'),template=field(form,'default_headline_template_id').value;return {context:{product_id:Number(form.dataset.productId)},selection:{mode:template?'template':'custom',...(template?{template_id:Number(template)}:{text:field(form,'default_headline_text').value}),preset_id:Number(field(form,'default_headline_preset_id').value),size:field(form,'default_headline_size').value}};});
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
   spec.background_per_line=presets.find(p=>String(p.id)===String(data.id))?.spec.background_per_line||false;
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
  catalogStatus=Array.isArray(state.headline_presets)&&Array.isArray(state.headline_styles)&&selectionStyleOptions().length?'READY':'ERROR';
  const next=JSON.stringify([presets,templates,styles]);hydrateOpenSelectionPanels();if(signature===next)return;signature=next;
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
  if(!draft.queue_capabilities?.can_edit&&h.enabled){
   const note=document.createElement('p');note.textContent=draft.queue_capabilities?.remote_effect_possible?'Este snapshot está congelado e o TikTok pode ter recebido o vídeo. Revise a pendência antes de qualquer nova tentativa.':'Este snapshot está congelado. Retome apenas a preparação validada ou cancele o trabalho local elegível antes de criar um novo Draft com outra headline.';box.append(note);
  }
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
     showPreviewFailure(preview,error,box);button.textContent='Tentar prévia novamente';
    }finally{button.disabled=requestedRevision!==previewRevision;}
   };
  }
  if(h.enabled){
   const comparison=document.createElement('details');comparison.className='headline-style-comparison';comparison.innerHTML='<summary>Comparar estilos</summary><label>Estilo da prévia<select data-comparison-style></select></label><label>Tamanho da prévia<select data-comparison-size>'+sizeOptions+'</select></label>';
   box.append(comparison);const select=comparison.querySelector('[data-comparison-style]');choices(select,true);
   comparison.querySelector('[data-comparison-size]').value=h.size||'STANDARD';
   comparisonControl(comparison,()=>({context:{draft_id:draft.id},selection:{...(select.value?{preset_id:Number(select.value)}:{}),size:comparison.querySelector('[data-comparison-size]').value}}),draft.queue_capabilities?.can_edit?'Comparar no vídeo':'Gerar prévia');
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
  field($('#productForm'),'default_headline_preset_id').dispatchEvent(new Event('change'));
 }
 function headlineOptions(productId,keep=false){
  return (keep?'<option value="keep">Manter snapshot atual</option>':'')+'<option value="none" selected>Sem headline</option><option value="product_default">Usar sugestão do produto</option>'+['Global','Do produto'].map((label,index)=>`<optgroup label="${label}">${templates.filter(t=>t.active&&(index?t.product_id===productId:t.product_id===null)).map(t=>`<option value="template:${t.id}">${esc(t.name)}</option>`).join('')}</optgroup>`).join('')+'<option value="custom">Personalizada</option>';
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
 function selectionControls(productId,value={mode:'none'},prefix=''){
  const mode=value.mode==='template'?`template:${value.template_id}`:value.mode||'none';
  return `<label>Headline<select data-headline-mode aria-label="Headline ${esc(prefix)}">${headlineOptions(productId)}</select></label><label data-headline-custom ${mode==='custom'?'':'hidden'}>Texto<textarea data-headline-text rows="2">${esc(value.text||'')}</textarea></label><div class="headline-choice-row"><label>Estilo<select data-headline-style>${selectionStyleMarkup(value.preset_id||'')}</select></label><label>Tamanho<select data-headline-size>${sizeOptions}</select></label><button type="button" data-headline-new>Criar nova headline</button></div>`;
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
  container.dataset.headlineProductId=String(productId||'');
  const mode=container.querySelector('[data-headline-mode]');mode.value=value.mode==='template'?`template:${value.template_id}`:value.mode||'none';
  container.querySelector('[data-headline-style]').value=value.preset_id||'';container.querySelector('[data-headline-size]').value=value.size||'STANDARD';
  hydrateSelectionStyle(container.querySelector('[data-headline-style]'));
  const update=e=>{if(e?.target?.matches('[data-headline-size]'))e.target.dataset.explicit='true';container.querySelector('[data-headline-custom]').hidden=mode.value!=='custom';if(e?.target?.matches('[data-headline-style]'))hydrateSelectionStyle(e.target);onChange(readSelection(container));};
  container.onchange=update;container.oninput=e=>{if(e.target.matches('textarea'))onChange(readSelection(container));};
  container.querySelector('[data-headline-new]').onclick=()=>openTemplate(null,{product_id:productId,text:container.querySelector('[data-headline-text]').value},id=>{mode.insertAdjacentHTML('beforeend',`<option value="template:${id}">${esc(templates.find(t=>t.id===id)?.name||'Headline salva')}</option>`);mode.value=`template:${id}`;update();});
 }
 function confirmBulk(selection,targets,keys){
  if(!targets.length)return false;
  const descriptions=new Set(targets.map(item=>{
   const product=state.products.find(p=>p.id===item.product_id)||{};
   const current=item.headline?{mode:item.headline.enabled?'custom':'none',text:item.headline.text,preset_id:item.headline.preset?.id,size:item.headline.size}:mediaChoices.get(item.drive_file_id)||{mode:'none'};
   const effective=keys?{...current,...Object.fromEntries(keys.filter(k=>selection[k]!==undefined).map(k=>[k,selection[k]]))}:selection;
   const text=effective.mode==='none'?'Sem headline':effective.mode==='custom'?effective.text:effective.mode==='template'?templates.find(t=>t.id===effective.template_id)?.text:templates.find(t=>t.id===product.default_headline_template_id)?.text||product.default_headline_text;
   const preset=presets.find(p=>p.id===(effective.preset_id||product.default_headline_preset_id));
   return `Headline: ${text||'Sugestão não configurada'}\nEstilo: ${preset?.name||'Sugestão do produto'}\nTamanho: ${effective.size||product.default_headline_size||'STANDARD'}`;
  }));
  return window.confirm(`Aplicar aos vídeos selecionados?\nVídeos afetados: ${targets.length}\n\n${[...descriptions].join('\n\n')}`);
 }
 function renderSelection(items){
  let section=$('#libraryHeadlineSelection');if(!section){section=document.createElement('section');section.id='libraryHeadlineSelection';$('#libraryBatchReadiness').before(section);}
  const signature=JSON.stringify([items.map(i=>[i.drive_file_id,i.filename,i.product_id]),templates,styles]);
  if(mediaSignature===signature)return;mediaSignature=signature;
  for(const key of [...mediaChoices.keys()])if(!items.some(i=>i.drive_file_id===key))mediaChoices.delete(key);
  section.innerHTML=`<p data-headline-counts></p><details><summary>Configuração individual (${items.length} vídeos)</summary><div id="headlineMediaBulk"></div>${items.map(i=>`<div class="headline-media-row" data-headline-media="${esc(i.drive_file_id)}"><strong>${esc(i.filename||i.drive_file_id)}</strong><small>${esc(i.product_short_name||i.product_name||'Sem produto')}</small>${selectionControls(i.product_id,mediaChoices.get(i.drive_file_id),i.filename)}<p data-headline-effective></p></div>`).join('')}</details>`;
  for(const item of items){const row=section.querySelector(`[data-headline-media="${CSS.escape(item.drive_file_id)}"]`);bindSelection(row,mediaChoices.get(item.drive_file_id)||{mode:'none'},item.product_id,value=>{mediaChoices.set(item.drive_file_id,value);selectionSummary(items);scheduleLibraryBatchPreview();});comparisonControl(row,()=>({context:{drive_file_id:item.drive_file_id},selection:readSelection(row)}));}
  const bulk=$('#headlineMediaBulk');bulk.innerHTML=`<h4>Configuração em lote · ${items.filter(i=>librarySelected.has(i.drive_file_id)).length} vídeos selecionados</h4>${selectionControls(null)}<div class="actions"><button type="button" data-bulk-text>Aplicar headline aos vídeos selecionados</button><button type="button" data-bulk-style>Aplicar estilo</button><button type="button" data-bulk-size>Aplicar tamanho</button></div>`;
  bindSelection(bulk,{mode:'none'},null,()=>{});
  for(const [selector,keys] of [['[data-bulk-text]',null],['[data-bulk-style]',['preset_id']],['[data-bulk-size]',['size']]])bulk.querySelector(selector).onclick=()=>{const targets=items.filter(i=>librarySelected.has(i.drive_file_id));const patch=readSelection(bulk);if(keys?.includes('size'))patch.size=bulk.querySelector('[data-headline-size]').value;if(!confirmBulk(patch,targets,keys))return;for(const item of targets){const old=mediaChoices.get(item.drive_file_id)||{mode:'none'};mediaChoices.set(item.drive_file_id,keys?{...old,...Object.fromEntries(keys.filter(k=>patch[k]!==undefined).map(k=>[k,patch[k]]))}:patch);}mediaSignature='';renderSelection(items);scheduleLibraryBatchPreview();};
  selectionSummary(items);
 }
 function selectionSummary(items){
  let enabled=0;
  for(const item of items){const value=mediaChoices.get(item.drive_file_id)||{mode:'none'},row=$('#libraryHeadlineSelection')?.querySelector(`[data-headline-media="${CSS.escape(item.drive_file_id)}"]`),product=state.products.find(p=>p.id===item.product_id)||{};
   const active=value.mode!=='none'&&(value.mode!=='product_default'||!!product.default_headline_enabled||!!product.headline_required);if(active)enabled++;
   const text=value.mode==='template'?templates.find(t=>t.id===value.template_id)?.text:value.mode==='custom'?value.text:templates.find(t=>t.id===product.default_headline_template_id)?.text||product.default_headline_text;
   const preset=presets.find(p=>p.id===(value.preset_id||product.default_headline_preset_id));
   const summary=row?.querySelector('[data-headline-effective]');if(summary)summary.textContent=active?`${text||'Texto não configurado'} · ${preset?.name||'Estilo não configurado'} · ${value.size||product.default_headline_size||'STANDARD'} · Render final pendente`:'Sem headline';
  }
  const counts=$('#libraryHeadlineSelection [data-headline-counts]');if(counts)counts.textContent=`${enabled} com headline · ${items.length-enabled} sem headline · Prévia e render final têm validações independentes`;
 }
 function selectionPayload(){return Object.fromEntries([...mediaChoices].filter(([id])=>librarySelected.has(id)));}
 function renderBulkDrafts(ids,entry){
  entry.innerHTML=`<h3>Headline dos selecionados</h3><div id="headlineDraftBulk">${selectionControls(null)}<div class="actions"><button type="button" data-bulk-text>Aplicar headline aos vídeos selecionados</button><button type="button" data-bulk-style>Aplicar estilo</button><button type="button" data-bulk-size>Aplicar tamanho</button></div></div>`;
  const box=entry.querySelector('#headlineDraftBulk'),frozen=ids.some(id=>!state.drafts.find(d=>d.id===id)?.queue_capabilities?.can_edit);
  box.dataset.headlineFrozen=String(frozen);
  bindSelection(box,{mode:'none'},null,()=>{});box.querySelectorAll('button,select,textarea').forEach(c=>c.disabled=frozen);
  if(frozen){const note=document.createElement('p');note.textContent='Há rascunhos congelados nesta seleção.';entry.append(note);}
  for(const [selector,keys] of [['[data-bulk-text]',null],['[data-bulk-style]',['preset_id']],['[data-bulk-size]',['size']]])box.querySelector(selector).onclick=()=>task(async()=>{const value=readSelection(box);if(keys?.includes('size'))value.size=box.querySelector('[data-headline-size]').value;if(!confirmBulk(value,ids.map(id=>state.drafts.find(d=>d.id===id)),keys))return;const patch=keys?Object.fromEntries(keys.filter(k=>value[k]!==undefined).map(k=>[k,value[k]])):value;await api('/headlines/bulk',{ids,patch});await refresh();notice('Headlines dos rascunhos atualizadas.');});
 }
 function summary(draft){const h=draft.headline;return h?.enabled?`<p>Headline: ${esc(h.template?.name||h.text)}</p><small>Estilo: ${esc(h.preset?.name||'Não configurado')}</small>`:'<p>Sem headline</p>';}
 return {init,render,inspect,edit,renderSelection,selectionPayload,summary,productEdited,catalogFailed};
})();
