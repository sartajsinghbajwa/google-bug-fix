/* Phase 14: White-Label Studio and a two-step personal event wizard. */
'use strict';
window.EventOSBranding=(()=>{
  const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const path=()=>prefix()+'/branding';
  const f=(label,name,value='',type='text',maxlength=180)=>'<label class="field"><span>'+h(label)+'</span><input name="'+name+'" type="'+type+'" maxlength="'+maxlength+'" value="'+h(value)+'"></label>';
  const options=(rows,chosen='',emptyText='Choose an option')=>'<option value="">'+h(emptyText)+'</option>'+rows.map(x=>'<option value="'+h(x.id)+'" '+(x.id===chosen?'selected':'')+'>'+h(x.name)+'</option>').join('');
  const renderPreset=t=>'<label class="brand-preset '+(t.available===false?'locked':'')+'"><input type="radio" name="template_id" value="'+h(t.id)+'" '+(t.available===false?'disabled':'')+'>'+
    '<span class="brand-theme" style="--preview-accent:'+h(t.accent||'#638579')+'"><span>✦</span></span><strong>'+h(t.name)+'</strong><small>'+h(t.description||'Your saved design')+'</small>'+
    (t.premium?'<em>Premium</em>':'')+(t.available===false?'<span class="tiny-note">Upgrade to unlock</span>':'')+'</label>';
  const cap=data=>data?.can_edit;
  const yes=()=>'<span class="brand-verified">Verified</span>';
  function render(){
    const data=state.brandingData||{},p=data.profile||{},domains=data.domains||[],templates=data.templates||[],editable=cap(data);
    return head('White-Label & Branding Studio','Your agency identity across client portals, event websites and mail.')+
      '<div class="brand-grid"><section class="card pad brand-settings"><h2>Brand identity</h2>'+
      '<p class="small-note">Create a consistent look for your clients. Your Honestly Zen Event Planning workspace records remain separate from other agencies.</p>'+
      '<form id="brand-profile-form" class="brand-fields">'+
      f('Agency display name','name',p.name||state.ws.name)+f('Tagline','tagline',p.tagline||'')+
      f('Primary color','primary_color',p.primary_color||'#23473d','color',7)+
      f('Accent color','accent_color',p.accent_color||'#b49a6a','color',7)+
      f('Email display name','email_name',p.email_name||'')+
      f('Reply-to email','reply_to',p.reply_to||'','email',255)+
      f('Portal and email footer','footer_text',p.footer_text||'')+
      '<div class="brand-field-wide"><button class="btn primary" '+(editable?'':'disabled')+' type="submit">Save branding</button></div></form></section>'+
      '<section class="card pad brand-preview"><h2>Live brand preview</h2>'+
      '<div class="brand-demo" style="--brand-primary:'+h(p.primary_color||'#23473d')+';--brand-accent:'+h(p.accent_color||'#b49a6a')+'">'+
      '<div class="brand-demo-top">'+(p.logo_url?'<img alt="" src="'+h(p.logo_url)+'?t='+encodeURIComponent(p.name||'brand')+'">':'<span class="brand-monogram">✦</span>')+
      '<strong>'+h(p.name||state.ws.name)+'</strong></div><div class="brand-demo-body"><small>PRIVATE CLIENT PORTAL</small><h3>Welcome to your event.</h3>'+
      '<p>'+h(p.tagline||'Thoughtfully planned, beautifully delivered.')+'</p><button type="button" disabled>View your event →</button></div>'+
      '<footer>'+h(p.footer_text||'Your event, our attention to detail.')+'</footer></div>'+
      '<form id="brand-logo-form" class="brand-logo-actions"><label class="field"><span>Agency logo (PNG, JPG, WebP; up to 1.5 MB)</span><input type="file" name="logo" accept="image/png,image/jpeg,image/webp" '+(editable?'':'disabled')+' required></label>'+
      '<button class="btn" type="submit" '+(editable?'':'disabled')+'>Upload logo</button>'+
      (p.logo_url?'<button class="btn" data-brand-action="remove-logo" '+(editable?'':'disabled')+' type="button">Remove logo</button>':'')+'</form>'+
      '<small class="tiny-note">Branded emails use your configured authenticated SMTP sender; changing the display name does not verify or replace its sending domain.</small></section></div>'+
      '<section class="card pad brand-domains"><div class="section-title"><h2>Custom domains</h2></div>'+
      '<p class="small-note">Add a public hostname, prove ownership with a DNS TXT record, then configure that hostname and HTTPS certificate with your hosting provider. DNS verification alone will not make the site live.</p>'+
      (data.can_manage_domains?'<form class="brand-domain-form" id="brand-domain-form">'+
      f('Hostname (no https://)','hostname','', 'text',253)+
      '<label class="field"><span>Use domain for</span><select name="target" id="brand-domain-target"><option value="website">A published event website</option><option value="client_portal">Agency client portals</option></select></label>'+
      '<label class="field"><span>Website event</span><select name="event_id" id="brand-domain-event">'+options(state.events||[])+'</select></label>'+
      '<button type="submit" class="btn primary">Add domain</button></form>':'')+
      '<div class="brand-domain-list">'+(domains.length?domains.map(d=>
      '<article class="brand-domain-item"><div><strong>'+h(d.hostname)+'</strong> '+(d.verified?yes():'<span class="brand-pending">Waiting for DNS</span>')+
      '<div class="tiny-note">'+h(d.target==='website'?'Event website':'Client portals')+'</div>'+
      '<p class="tiny-note">DNS TXT record name: <code>'+h(d.txt_host)+'</code></p>'+
      '<p class="tiny-note">TXT value: <code>'+h(d.txt_value)+'</code></p>'+
      (d.verified?'<p class="tiny-note">Verified ownership — configure a custom domain and TLS on Render or your web host before sharing links.</p>':'')+
      '</div>'+(data.can_manage_domains?'<div class="brand-actions">'+
      (!d.verified?'<button class="btn" data-brand-action="verify-domain" data-id="'+h(d.id)+'">Check DNS</button>':'')+
      '<button class="btn" data-brand-action="remove-domain" data-id="'+h(d.id)+'">Remove</button></div>':'')+'</article>').join(''):
      '<p class="small-note">No domains connected yet. Your current Honestly Zen Event Planning links continue to work.</p>')+'</div></section>'+
      '<section class="card pad brand-library"><div class="section-title"><h2>Premium & reusable templates</h2></div>'+
      '<p class="small-note">Apply a ready-made look to an existing event website without overwriting its story, accommodations, or FAQs. Your saved reusable templates are private to your workspace.</p>'+
      '<div class="brand-template-grid">'+templates.map(renderPreset).join('')+'</div>'+
      (editable?'<form id="brand-apply-form" class="brand-apply-form">'+
      '<label class="field"><span>Apply selected look to event</span><select name="event_id" required>'+options(state.events||[])+'</select></label>'+
      '<button class="btn primary" type="submit">Apply to website</button></form>'+
      '<form id="brand-save-template-form" class="brand-apply-form">'+
      '<label class="field"><span>Save a design from this event</span><select name="event_id" required>'+options(state.events||[])+'</select></label>'+
      f('Reusable template name','name','', 'text',80)+'<button class="btn" type="submit">Save reusable template</button></form>':'')+
      '<p class="tiny-note">Premium looks are available according to your eligible subscription. Designs remain drafts until you publish their event website.</p></section>';
  }
  async function requestAction(action,id){
    try{
      if(action==='remove-logo'){
        if(!confirm('Remove the agency logo?'))return;
        await api(path()+'/logo',{method:'DELETE'});
      }else if(action==='verify-domain'){
        await api(path()+'/domains/'+encodeURIComponent(id)+'/verify',{method:'POST'});
      }else if(action==='remove-domain'){
        if(!confirm('Disconnect this domain from your workspace?'))return;
        await api(path()+'/domains/'+encodeURIComponent(id),{method:'DELETE'});
      }else return;
      toast('Branding updated');await refresh();
    }catch(err){toast(err.message)}
  }
  async function readLogo(file){
    if(!file||file.size>1500000)throw Error('Select an image smaller than 1.5 MB');
    if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw Error('Choose a PNG, JPG or WebP image');
    return await new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onload=()=>resolve(String(reader.result).split(',')[1]);
      reader.onerror=()=>reject(Error('Image could not be read'));
      reader.readAsDataURL(file);
    });
  }
  async function submit(e){
    const form=e.target,id=form.id;
    if(!id.startsWith('brand-'))return;
    e.preventDefault();
    const btn=form.querySelector('[type=submit]');if(btn)btn.disabled=true;
    const obj=Object.fromEntries(new FormData(form));
    try{
      if(id==='brand-profile-form')await api(path(),{method:'PUT',body:obj});
      else if(id==='brand-logo-form'){
        const file=form.elements.logo.files[0];
        await api(path()+'/logo',{method:'POST',body:{data_base64:await readLogo(file),content_type:file.type}});
      }else if(id==='brand-domain-form'){
        await api(path()+'/domains',{method:'POST',body:{hostname:obj.hostname,target:obj.target,event_id:obj.target==='website'?obj.event_id:null}});
      }else if(id==='brand-apply-form'){
        const choice=document.querySelector('.brand-library input[name=template_id]:checked');
        if(!choice)throw Error('Select a design first');
        await api(prefix()+'/events/'+encodeURIComponent(obj.event_id)+'/branding/apply-template',{method:'POST',body:{template_id:choice.value}});
      }else if(id==='brand-save-template-form'){
        await api(path()+'/templates',{method:'POST',body:obj});
      }else return;
      toast('Saved successfully');await refresh();
    }catch(err){toast(err.message);if(btn)btn.disabled=false}
  }
  let wizardStep=0,wizardData={},wizardTemplates=[];
  function wizardRender(){
    const root=document.getElementById('modalRoot');
    if(!root)return;
    const types=['Birthday','Wedding','Anniversary','Baby shower','Private party','Festival','Fundraiser','Corporate meeting','Other'];
    const basic='<label class="field wide"><span>What are we celebrating?</span><input name="name" maxlength="160" required placeholder="e.g. Sarah’s birthday" value="'+h(wizardData.name||'')+'"></label>'+
      '<label class="field"><span>Event type</span><select name="type">'+types.map(t=>'<option '+(wizardData.type===t?'selected':'')+'>'+h(t)+'</option>').join('')+'</select></label>'+
      '<label class="field"><span>Event date</span><input name="start_date" type="date" required value="'+h(wizardData.start_date||'')+'"></label>'+
      '<label class="field wide"><span>Venue or location (optional)</span><input name="location" maxlength="500" value="'+h(wizardData.location||'')+'"></label>'+
      '<label class="field"><span>Estimated guests</span><input name="expected_guests" type="number" min="0" max="100000" step="1" value="'+h(wizardData.expected_guests??25)+'" required></label>';
    const choose='<p class="small-note">Choose a design to start with. You can change every detail later in the Event Website editor.</p>'+
      '<div class="brand-template-grid">'+wizardTemplates.map(t=>renderPreset({...t})).join('')+'</div>';
    root.innerHTML='<div class="modal-overlay brand-wizard-overlay" data-wizard-dismiss="yes"><section class="modal brand-wizard" role="dialog" aria-modal="true" aria-labelledby="wizardTitle">'+
      '<header class="modal-head"><div><small>PERSONAL EVENT · STEP '+(wizardStep+1)+' OF 2</small><h2 id="wizardTitle">'+(wizardStep===0?'Tell us about your event':'Make it yours')+'</h2></div>'+
      '<button type="button" class="xclose" data-wizard-close="yes" aria-label="Close">×</button></header>'+
      '<form id="brand-wizard-form" class="modal-body"><div class="brand-wizard-progress"><span class="active"></span><span class="'+(wizardStep?'active':'')+'"></span></div>'+
      '<div class="brand-wizard-fields">'+(wizardStep===0?basic:choose)+'</div>'+
      '<footer class="brand-wizard-footer">'+(wizardStep?'<button class="btn" type="button" data-wizard-back="yes">← Back</button>':'<button class="btn" type="button" data-wizard-close="yes">Cancel</button>')+
      '<button class="btn primary" type="submit">'+(wizardStep===0?'Choose a design →':'Create my event →')+'</button></footer></form></section></div>';
    if(wizardStep===1){
      const chosen=root.querySelector('input[name=template_id][value="'+(wizardData.template_id||'')+'"]');
      if(chosen&&!chosen.disabled)chosen.checked=true;
      else {const first=root.querySelector('input[name=template_id]:not(:disabled)');if(first)first.checked=true;}
    }
  }
  async function wizard(){
    if(state.ws?.type!=='personal'){openModal('event');return}
    try{
      const list=await api(prefix()+'/branding/templates');
      wizardTemplates=list.templates||[];
      wizardStep=0;wizardData={type:'Birthday',expected_guests:25};
      wizardRender();
    }catch(err){toast(err.message)}
  }
  async function wizardSubmit(e){
    e.preventDefault();
    const form=e.target,data=Object.fromEntries(new FormData(form));
    if(wizardStep===0){
      const guests=Number(data.expected_guests);
      if(!Number.isInteger(guests)||guests<0||guests>100000){toast('Enter a valid guest count');return}
      wizardData={...wizardData,...data,expected_guests:guests};
      wizardStep=1;wizardRender();return;
    }
    const id=data.template_id;
    if(!id){toast('Choose a free or unlocked design');return}
    const choice=wizardTemplates.find(t=>t.id===id);
    if(!choice||choice.available===false){toast('This design is locked');return}
    const submit=form.querySelector('[type=submit]');submit.disabled=true;
    try{
      const event=await api(prefix()+'/quick-create',{method:'POST',body:{...wizardData,template_id:id}});
      closeModal();toast('Your event is ready!');go('overview',event.id);
    }catch(err){toast(err.message);submit.disabled=false}
  }
  document.addEventListener('click',e=>{
    const control=e.target.closest('[data-brand-action]');if(control){requestAction(control.dataset.brandAction,control.dataset.id||'');return}
    if(e.target.closest('[data-wizard-close]') || e.target.matches('[data-wizard-dismiss]')){closeModal();return}
    if(e.target.closest('[data-wizard-back]')){wizardStep=0;wizardRender();return}
  });
  document.addEventListener('submit',e=>{if(e.target.id==='brand-wizard-form')wizardSubmit(e);else submit(e)});
  document.addEventListener('change',e=>{
    if(e.target.id==='brand-domain-target'){
      const event=document.getElementById('brand-domain-event');
      if(event){event.disabled=e.target.value==='client_portal';event.required=e.target.value==='website'}
    }
  });
  return {render,wizard};
})();
