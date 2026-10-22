/* Organizer website studio with templates, sections, details and true preview. */
'use strict';
(function(){
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const d=()=>state.websiteData||{};
 const apiPath=()=>evpath()+'/website';
 const sections={story:'Our story',schedule:'Schedule',venue:'Venue and directions',stay:'Accommodation',faq:'Questions & answers',rsvp:'RSVP invitation link',gallery:'Live photos',gifts:'Gift registry'};
 const palettes={garden:'#658976',classic:'#a47d5d',modern:'#4e8383',night:'#bda57d',minimal:'#333f3a'};
 let selectedTab='design',draftSlug='',lastEventId=null;
 const isEditor=()=>d().can_edit;
 function textInput(label,key,max=250,type='text'){
  return '<label class="field"><span>'+esc(label)+'</span><input data-website-field="'+key+'" type="'+type+'" value="'+esc(d().content?.[key]||'')+'" maxlength="'+max+'" '+(isEditor()?'':'disabled')+'></label>';
 }
 function area(label,key,max=1200,help=''){
  return '<label class="field"><span>'+esc(label)+'</span><textarea data-website-field="'+key+'" rows="4" maxlength="'+max+'" '+(isEditor()?'':'disabled')+'>'+esc(d().content?.[key]||'')+'</textarea>'+(help?'<small>'+esc(help)+'</small>':'')+'</label>';
 }
 function cardRow(p,i){
  return '<div class="site-editor-mini" data-site-row="accommodations"><div class="site-editor-mini-head"><strong>Stay '+(i+1)+'</strong><button class="btn tiny danger" type="button" onclick="websiteRemoveRow(\'accommodations\','+i+')">Remove</button></div>'+
   '<div class="site-editor-fields"><label>Accommodation name<input name="name" maxlength="110" value="'+esc(p.name)+'"></label><label>Address<input name="address" maxlength="230" value="'+esc(p.address)+'"></label><label>Booking link (HTTPS)<input name="url" type="url" maxlength="500" value="'+esc(p.url)+'"></label><label>Notes<textarea name="note" maxlength="350">'+esc(p.note)+'</textarea></label></div></div>';
 }
 function faqRow(p,i){
  return '<div class="site-editor-mini" data-site-row="faq"><div class="site-editor-mini-head"><strong>Question '+(i+1)+'</strong><button class="btn tiny danger" type="button" onclick="websiteRemoveRow(\'faq\','+i+')">Remove</button></div>'+
  '<label>Question<input name="question" maxlength="180" value="'+esc(p.question)+'"></label><label>Answer<textarea name="answer" maxlength="1000">'+esc(p.answer)+'</textarea></label></div>';
 }
 function collectRows(name){
  return [...document.querySelectorAll('[data-site-row="'+name+'"]')].map(el=>{
   const entries=name==='faq'?['question','answer']:['name','address','url','note'];
   return Object.fromEntries(entries.map(k=>[k,el.querySelector('[name="'+k+'"]')?.value||'']));
  });
 }
 function activeContent(){
  const data=typeof structuredClone==='function'?structuredClone(d().content):JSON.parse(JSON.stringify(d().content));
  document.querySelectorAll('[data-website-field]').forEach(el=>{if(el.dataset.websiteField==='accent')data.accent=el.value;else data[el.dataset.websiteField]=el.value});
  const order=[...document.querySelectorAll('[data-website-section]')];
  if(order.length) {
   data.section_order=order.map(e=>e.dataset.websiteSection);
   order.forEach(el=>{data.sections[el.dataset.websiteSection]=el.querySelector('input[type=checkbox]').checked;});
  }
  if(document.getElementById('websiteHotelRows'))data.accommodations=collectRows('accommodations');
  if(document.getElementById('websiteFaqRows'))data.faq=collectRows('faq');
  return data;
 }
 function editTabContent(){
  if(selectedTab==='design')return '<div class="site-builder-block"><h3>Choose a beautiful starting point</h3><p class="tiny-note">Select a theme. Your event information stays connected to the CRM.</p><div class="site-theme-grid">'+Object.keys(palettes).map(theme=>
    '<button type="button" class="site-theme'+(d().content.template===theme?' active':'')+'" data-theme-pick="'+theme+'" onclick="websiteTheme(\''+theme+'\')">'+
    '<span class="site-theme-swatch '+theme+'"><span>✿</span></span><strong>'+theme[0].toUpperCase()+theme.slice(1)+'</strong></button>').join('')+'</div></div>'+
    '<div class="site-builder-block"><h3>Website address</h3><label class="field"><span>Custom share URL name</span><div class="site-slug-input"><span>/event/</span><input id="siteSlug" type="text" pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="64" value="'+esc(draftSlug||d().url.split('/')[2])+'" '+(isEditor()?'':'disabled')+'><span>/private-code</span></div></label><p class="tiny-note">Use lowercase letters, numbers and hyphens. Changing this address invalidates earlier shared links.</p><button type="button" class="btn tiny" onclick="websiteRotate()">Regenerate private access link</button></div>'+
    '<div class="site-builder-block"><h3>Brand & colors</h3>'+
    '<label class="field"><span>Accent color</span><input data-website-field="accent" type="color" value="'+esc(d().content.accent)+'" '+(isEditor()?'':'disabled')+'></label>'+
    textInput('Website headline','title',140)+textInput('Short subtitle','subtitle',200)+area('Welcome message','welcome',500)+
    textInput('Footer message','footer',180)+'</div>'+
    '<div class="site-builder-block"><h3>Cover photo</h3>'+
    (d().has_cover?'<div class="site-cover-thumbnail" style="background-image:url(&quot;/api'+apiPath()+'/cover&quot;)"></div>':'<p class="small-note">Add a special photo to create a full-width hero on your event website.</p>')+
    (isEditor()?'<input type="file" accept="image/jpeg,image/png,image/webp" id="siteCoverFile" onchange="websiteCover(this.files[0])"><p class="tiny-note">JPEG, PNG or WebP, up to 8 MB.</p>'+
     (d().has_cover?'<button type="button" class="btn danger tiny" onclick="websiteDeleteCover()">Remove cover photo</button>':''):'')+'</div>';
  if(selectedTab==='content')return '<div class="site-builder-block"><h3>Your story and details</h3>'+
    textInput('Story heading','story_title',100)+area('Our story','story',3000)+
    textInput('Dress code','dress_code',500)+area('Parking information','parking',1000)+area('Travel and transportation','travel',1000)+textInput('Contact information','contact',200)+
    '<p class="small-note">Venue, event date and itinerary are synchronized from Event Details and Schedule. Edit those sections from their Honestly Zen Event Planning menus.</p>'+
    '<button class="btn" type="button" onclick="go(\'details\',state.eventId)">Edit venue or date</button> <button class="btn" type="button" onclick="go(\'schedule\',state.eventId)">Edit schedule</button></div>'+
    '<div class="site-builder-block"><h3>Accommodations</h3><div id="websiteHotelRows">'+(d().content.accommodations||[]).map(cardRow).join('')+'</div>'+
    (isEditor()?'<button type="button" class="btn" onclick="websiteAddRow(\'accommodations\')">+ Add accommodation</button>':'')+'</div>'+
    '<div class="site-builder-block"><h3>Questions and answers</h3><div id="websiteFaqRows">'+(d().content.faq||[]).map(faqRow).join('')+'</div>'+
    (isEditor()?'<button type="button" class="btn" onclick="websiteAddRow(\'faq\')">+ Add FAQ</button>':'')+'</div>';
  return '<div class="site-builder-block"><h3>Visible sections & order</h3><p class="small-note">Use the arrows to reorder sections. Turn a section off to hide it from guests.</p>'+
   '<div class="site-section-list">'+(d().content.section_order||[]).map((key,i)=>'<div class="site-section-row" data-website-section="'+key+'">'+
   '<label><input type="checkbox" '+(d().content.sections[key]?'checked':'')+' '+(isEditor()?'':'disabled')+'> '+esc(sections[key])+'</label>'+
   (isEditor()?'<div><button type="button" onclick="websiteMoveSection(\''+key+'\',-1)" aria-label="Move section up" '+(i===0?'disabled':'')+'>↑</button><button type="button" onclick="websiteMoveSection(\''+key+'\',1)" aria-label="Move section down" '+(i===7?'disabled':'')+'>↓</button></div>':'')+'</div>').join('')+'</div></div>'+
   '<div class="site-builder-block"><h3>Guest experience</h3><p class="small-note">RSVPs always use each guest’s existing private invitation code. Photos and gifts only appear when their event modules are enabled.</p>'+
   '<button class="btn" onclick="go(\'guests\',state.eventId)">Open guests and RSVP</button> <button class="btn" onclick="go(\'gallery\',state.eventId)">Photo gallery</button> <button class="btn" onclick="go(\'registry\',state.eventId)">Gift registry</button></div>';
 }
 function previewTile(){
  const c=d().content||{},e=d().event||{};
  return '<div class="site-mock" data-preview-theme="'+esc(c.template)+'" style="--site-preview-accent:'+esc(c.accent)+'">'+
   '<div class="site-mock-cover"'+(d().has_cover?' style="background-image:url(&quot;/api'+apiPath()+'/cover&quot;)"':'')+'>'+
   '<div class="site-mock-text"><small>YOU ARE INVITED TO CELEBRATE</small><h2>'+esc(c.title||e.name||'A special celebration')+'</h2>'+
   '<p>'+esc(c.subtitle||'Together is a beautiful place to be.')+'</p><strong>'+esc(e.start_date||'')+'</strong>'+
   '<span>'+esc(e.location||'Location to be announced')+'</span><div class="site-mock-button">Celebrate with us ↗</div></div></div>'+
   '<div class="site-mock-sections">'+(c.section_order||[]).filter(k=>c.sections[k]).map(k=>'<span>'+esc(sections[k])+'</span>').join('')+'</div></div>';
 }
 function render(){
  if(lastEventId!==state.eventId){lastEventId=state.eventId;draftSlug='';selectedTab='design'}
  const data=d(),can=data.can_edit,full=location.origin+data.url,tab=selectedTab;
  if(!draftSlug&&data.url)draftSlug=data.url.split('/')[2];
  return head('Event website studio','Build a beautiful, mobile-ready website for your celebration. Every event has its own shareable page.')+
   '<div class="site-status-strip"><span class="site-status '+(data.published?'published':'draft')+'">'+(data.published?'● PUBLISHED':'○ PRIVATE DRAFT')+'</span>'+
   '<span>Event website link</span><input aria-label="Event website URL" readonly value="'+esc(full)+'">'+
   '<button class="btn" type="button" onclick="websiteCopy()">Copy link</button>'+
   '<button class="btn" type="button" onclick="websiteOpenPreview()">'+(data.published?'Open website ↗':'Preview draft ↗')+'</button>'+
   (can?'<button class="btn '+(data.published?'':'primary')+'" type="button" onclick="websitePublish()">'+(data.published?'Unpublish':'Publish website')+'</button>':'')+'</div>'+
   '<div class="site-builder-grid"><section class="card site-builder-column"><div class="site-builder-top"><h2>Customize your website</h2><p>Personalize the look, content and sections.</p></div>'+
   '<div class="site-builder-tabs" role="tablist">'+[['design','Design'],['content','Details'],['sections','Sections']].map(([key,name])=>'<button role="tab" type="button" aria-selected="'+(tab===key)+'" class="'+(tab===key?'active':'')+'" onclick="websiteTab(\''+key+'\')">'+name+'</button>').join('')+'</div>'+
   '<div class="site-builder-scroll">'+editTabContent()+'</div>'+
   (can?'<div class="site-builder-save"><button class="btn primary" type="button" onclick="websiteSave()">Save website changes</button><p class="tiny-note">Saving does not publish. Guests see changes only while your site is published.</p></div>':'')+'</section>'+
   '<aside class="site-preview-column"><div class="site-preview-heading"><strong>Live design preview</strong><span>Mobile-friendly layout</span></div>'+previewTile()+
   '<p class="small-note">The preview reflects your last saved changes. Open the full website to see the complete guest experience.</p>'+
   '<div class="site-share-qr"><img loading="lazy" alt="QR code for event website" src="/api'+apiPath()+'/qr.png"><div><strong>Share with a QR code</strong><p>Download the QR code for your invitation cards, event signage, or printed programs. Only published websites open for guests.</p><a class="btn tiny" target="_blank" href="/api'+apiPath()+'/qr.png">Open QR image</a></div></div>'+
   '<div class="site-domain-card"><h3>Custom domain</h3><p>Your Honestly Zen Event Planning website has a secure shareable address. Connecting your own domain will require domain ownership verification and DNS configuration and is not active in this test release.</p></div></aside></div>';
 }
 function synchronize(){
  const c=d().content;
  if(!c)return;
  try{d().content=activeContent(); const el=document.getElementById('siteSlug');if(el)draftSlug=el.value;return true;}catch(e){toast(e.message);return false}
 }
// Updating the studio HTML string alone does not update the CRM page.
 // Always ask the main Honestly Zen Event Planning renderer to replace the live DOM after edits.
 const repaint=()=>{if(state.page==='website')window.render()};
 window.websiteTab=k=>{if(!['design','content','sections'].includes(k)||synchronize()===false)return;selectedTab=k;repaint()};
 window.websiteTheme=t=>{
  if(!palettes[t]||!isEditor())return;
  if(synchronize()===false)return;d().content.template=t;d().content.accent=palettes[t];repaint();
 };
 window.websiteAddRow=key=>{
  if(!isEditor())return;
  if(synchronize()===false)return;
  const arr=d().content[key];if(arr.length>=(key==='faq'?20:12)){toast('Maximum entries reached');return}
  arr.push(key==='faq'?{question:'',answer:''}:{name:'',address:'',note:'',url:''});repaint();
 };
 window.websiteRemoveRow=(key,i)=>{
  if(!isEditor()||synchronize()===false)return;d().content[key].splice(i,1);repaint();
 };
 window.websiteMoveSection=(key,step)=>{
  if(!isEditor()||synchronize()===false)return;const a=d().content.section_order,i=a.indexOf(key),j=i+step;
  if(i<0||j<0||j>=a.length)return;
  [a[i],a[j]]=[a[j],a[i]];repaint();
 };
 window.websiteSave=async silent=>{
  if(!isEditor())return;
  const content=activeContent();
  const slug=document.getElementById('siteSlug')?.value||draftSlug||d().url.split('/')[2];
  try{
   const result=await api(apiPath(),{method:'PUT',body:{content,slug}});
   state.websiteData=result;draftSlug=slug;
   if(!silent)toast('Event website saved');
   repaint();
   return true;
  }catch(e){toast(e.message);return false}
 };
 window.websitePublish=async()=>{
  if(!isEditor())return;
  if(!d().published){const okay=await window.websiteSave(true);if(!okay)return}
  if(d().published&&!confirm('Unpublish this event website? Guests will no longer be able to open it.'))return;
  const publishing=!d().published;
  try{await api(apiPath()+'/publish',{method:'PATCH',body:{published:publishing}});
  state.websiteData=await api(apiPath());toast(publishing?'Website published':'Website unpublished');repaint()}
  catch(e){toast(e.message)}
 };
 window.websiteCopy=async()=>{try{await navigator.clipboard.writeText(location.origin+d().url);toast('Website link copied')}catch{toast('Copy the website link from the address field')}};
 window.websiteOpenPreview=()=>{const target=d().url+(d().published?'':'?preview=true');window.open(target,'_blank','noopener,noreferrer')};
 window.websiteRotate=async()=>{if(!confirm('Replace the event website link? Existing links will stop working.'))return;try{await api(apiPath()+'/rotate-link',{method:'POST'});state.websiteData=await api(apiPath());draftSlug=state.websiteData.url.split('/')[2];repaint();toast('Website link changed')}catch(e){toast(e.message)}};
 window.websiteCover=async file=>{
  if(!file)return;if(file.size>8*1024*1024){toast('Photo must be smaller than 8 MB');return}
  const raw=new Uint8Array(await file.arrayBuffer()),chunks=[];
  for(let i=0;i<raw.length;i+=32768)chunks.push(String.fromCharCode(...raw.subarray(i,i+32768)));
  try{await api(apiPath()+'/cover',{method:'POST',body:{content_type:file.type,data_base64:btoa(chunks.join(''))}});
   state.websiteData=await api(apiPath());toast('Cover photo uploaded');repaint()}
  catch(err){toast(err.message)}
 };
 window.websiteDeleteCover=async()=>{
  if(!confirm('Remove the website cover photo?'))return;
  try{await api(apiPath()+'/cover',{method:'DELETE'});state.websiteData=await api(apiPath());repaint();toast('Cover removed')}
  catch(e){toast(e.message)}
 };
 window.EventOSWebsites={render};
})();