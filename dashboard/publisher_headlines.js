/* Local headline configuration and explicit, local-only preview requests. */
const PublisherHeadlines = (() => {
 const defaults={font_ref:'',font_sha256:'',font_face_index:0,font_size_ratio:0.05,font_weight:400,text_color:'#FFFFFF',alignment:'center',x:0.5,y:0.15,margin_x:0.05,margin_y:0.05,max_width:0.9,max_lines:3,wrapping:'word',background_color:null,background_padding_ratio:0.01,stroke_color:null,stroke_width_ratio:0.002,shadow_color:null,shadow_offset_x:0.002,shadow_offset_y:0.002,shadow_blur_ratio:0.004};
 const numeric={font_face_index:['Face da fonte',0,255,1],font_size_ratio:['Tamanho relativo',0.005,0.3,0.001],font_weight:['Peso',100,900,100],x:['Posicao X',0,1,0.01],y:['Posicao Y',0,1,0.01],margin_x:['Margem horizontal',0,0.45,0.01],margin_y:['Margem vertical',0,0.45,0.01],max_width:['Largura maxima',0.01,1,0.01],max_lines:['Maximo de linhas',1,8,1],background_padding_ratio:['Espaco do fundo',0,0.1,0.001],stroke_width_ratio:['Espessura do contorno',0,0.05,0.001],shadow_offset_x:['Sombra X',-0.1,0.1,0.001],shadow_offset_y:['Sombra Y',-0.1,0.1,0.001],shadow_blur_ratio:['Desfoque da sombra',0,0.1,0.001]};
 let presets=[],signature='';
 function choices(select,keep=false){
  const value=select.value;
  select.innerHTML=(keep?'<option value="">Manter snapshot atual</option>':'<option value="">Sem preset</option>')+presets.filter(p=>!keep||p.active||String(p.id)===value).map(p=>`<option value="${p.id}">${esc(p.name)} / v${p.current_version}${p.active?'':' (arquivado)'}</option>`).join('');
  select.value=value;
 }
 function init(){
  const product=document.createElement('fieldset');product.id='productHeadline';
  product.innerHTML='<legend>Headline no video</legend><label class="check"><input type="checkbox" name="default_headline_enabled">Ativada</label><label>Texto padrao<textarea name="default_headline_text" rows="3"></textarea></label><label>Preset visual<select name="default_headline_preset_id"></select></label><label class="check"><input type="checkbox" name="headline_required">Headline obrigatoria</label>';
  $('#productForm').insertBefore(product,$('#productForm').querySelector('[type=submit]'));
  const section=document.createElement('section');section.id='headlinePresets';
  section.innerHTML=`<h3>Presets visuais de headline</h3><div id="headlinePresetList" class="record-list"></div><form id="headlinePresetForm" class="editor"><input name="id" type="hidden"><input name="expected_version" type="hidden"><label>Nome<input name="name" maxlength="120" required></label><label class="check"><input name="active" type="checkbox" checked>Ativo</label><label>Referencia da fonte<input name="font_ref" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,127}" required></label><details><summary>Identidade da fonte</summary><label>SHA-256<input name="font_sha256" pattern="[0-9a-fA-F]{64}" required></label></details><div class="headline-fields">${Object.entries(numeric).map(([key,[label,min,max,step]])=>`<label>${label}<input name="${key}" type="number" min="${min}" max="${max}" step="${step}" value="${defaults[key]}" required></label>`).join('')}<label>Cor do texto<input type="color" name="text_color" value="#ffffff"></label><label>Alinhamento<select name="alignment"><option value="left">Esquerda</option><option value="center" selected>Centro</option><option value="right">Direita</option></select></label><label>Quebra de linha<select name="wrapping"><option value="word">Por palavra</option><option value="explicit">Somente explicita</option></select></label>${[['background','Fundo'],['stroke','Contorno'],['shadow','Sombra']].map(([key,label])=>`<label class="check"><input type="checkbox" name="${key}_enabled">${label}<input type="color" name="${key}_color" value="#000000" aria-label="Cor: ${label}"></label>`).join('')}</div><div class="actions"><button type="submit" class="primary">Salvar versao</button><button type="reset">Novo preset visual</button></div></form>`;
  $('#presets').append(section);
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
  presets=state.headline_presets||[];
  const next=JSON.stringify(presets);if(signature===next)return;signature=next;
  choices($('#productHeadline select'));
  $('#headlinePresetList').innerHTML=presets.map(p=>`<div class="record"><div>${esc(p.name)}<small>v${p.current_version} / ${p.active?'Ativo':'Arquivado'}</small></div><button type="button" data-headline-preset="${p.id}">Editar</button></div>`).join('')||'<p>Nenhum preset visual.</p>';
 }
 function edit(ids){
  const entry=$('#draftHeadlineEntry');entry.replaceChildren();if(ids.length!==1)return;
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
  if(h.enabled){
   const button=document.createElement('button');button.type='button';button.textContent='Gerar pr\u00e9via';
   const preview=document.createElement('div');preview.className='headline-preview';preview.setAttribute('aria-live','polite');
   box.append(button,preview);
   button.onclick=async()=>{
    button.disabled=true;preview.textContent='Gerando pr\u00e9via local...';
    try{
     const result=await api(`/drafts/${draft.id}/headline-preview`,{});
     if(result.state!=='READY')throw new Error('Headline desativada.');
     const image=document.createElement('img');image.alt='Pr\u00e9via da headline deste draft';image.src=result.image;
     preview.replaceChildren(image);
    }catch(error){
     const message=String(error.message||error);
     preview.textContent=message.includes('NOT_CONFIGURED')?'Pr\u00e9via local ainda n\u00e3o habilitada neste ambiente.':message.includes('LOCAL_SOURCE')?'O v\u00eddeo precisa estar dispon\u00edvel localmente. Nenhum download foi iniciado.':'N\u00e3o foi poss\u00edvel gerar a pr\u00e9via. Confira fonte, texto e margens.';
    }finally{button.disabled=false;}
   };
  }
  const form=box.querySelector('#draftHeadlineForm');if(!form)return;
  choices(field(form,'headline_preset_id'),true);
  form.onsubmit=e=>{e.preventDefault();task(async()=>{
   const data=formData(form);if(h.required)data.enabled=true;
   if(data.headline_preset_id)data.preset_id=data.headline_preset_id;
   delete data.headline_preset_id;
   await api(`/drafts/${draft.id}/headline`,data);await refresh();
   inspect(state.drafts.find(d=>d.id===draft.id),target);edit([draft.id]);notice('Headline local atualizada.');
  });};
 }
 return {init,render,inspect,edit};
})();
