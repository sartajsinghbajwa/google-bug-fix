/* Honestly Zen Event Planning event photo galleries: organizer curation & image upload. */
'use strict';
(function(){
 const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const d=()=>state.galleryData||{settings:{},photos:[],counts:{},can_edit:false};
 const url=()=>evpath()+'/gallery';
 const img=(p,kind='thumb')=>'/api'+url()+'/photos/'+encodeURIComponent(p.id)+'/'+kind;
 const gallery=()=>{
  const x=d(),s=x.settings,photos=x.photos||[],pending=photos.filter(p=>p.status==='pending');
  return head('Live Photo Gallery','Share memories during the event, review guest photos, and publish an approved live gallery.',
   x.can_edit?button('Upload photos',"document.getElementById('galleryFile')?.click()",true):'')+
  '<div class="experience-stats">'+[['Published',x.counts?.approved||0],['Awaiting approval',x.counts?.pending||0],['All uploads',photos.length]].map(([n,v])=>'<div><strong>'+v+'</strong><span>'+n+'</span></div>').join('')+'</div>'+
  '<div class="experience-grid"><section class="card pad"><h2>Gallery settings</h2>'+
  '<p class="small-note">Guest uploads are private until approved by staff when moderation is enabled. Only photos you publish appear in the shared gallery.</p>'+
  '<label class="exp-check"><input type="checkbox" '+(s.enabled?'checked':'')+' '+(x.can_edit?'':'disabled')+' onchange="gallerySetting(\'enabled\',this.checked)"> Publish gallery link</label>'+
  '<label class="exp-check"><input type="checkbox" '+(s.allow_guest_uploads?'checked':'')+' '+(x.can_edit?'':'disabled')+' onchange="gallerySetting(\'allow_guest_uploads\',this.checked)"> Allow guests to upload photos</label>'+
  '<label class="exp-check"><input type="checkbox" '+(s.require_approval?'checked':'')+' '+(x.can_edit?'':'disabled')+' onchange="gallerySetting(\'require_approval\',this.checked)"> Review uploads before publishing</label>'+
  '<label class="field"><span>Gallery title</span><input id="galleryTitle" maxlength="100" value="'+h(s.title||'Our Event Memories')+'" '+(x.can_edit?'':'disabled')+'></label>'+
  (x.can_edit?'<button class="btn" onclick="gallerySaveTitle()">Save title</button>':'')+
  '<div class="experience-share"><strong>Share gallery with guests</strong><input readonly value="'+h(location.origin+x.share_url)+'" aria-label="Gallery link">'+
  '<button class="btn" type="button" onclick="copyExperienceLink(\''+h(x.share_url)+'\')">Copy gallery link</button>'+
  '<button class="btn" type="button" onclick="window.open(\''+h(x.share_url)+'\',\'_blank\')">Open gallery</button>'+
  (x.can_edit?'<button class="btn tiny" type="button" onclick="galleryRotate()">Regenerate guest link</button>':'')+'</div>'+
  '<p class="tiny-note">Upload limits: 8 MB each, 250 photos and 120 MB per event. Images are resized and location metadata removed. The demo uses database storage.</p></section>'+
  '<section class="card pad"><h2>Upload & moderation</h2>'+
  '<input id="galleryFile" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onchange="galleryUploadFiles(this.files)">'+
  (x.can_edit?'<button class="btn primary" onclick="document.getElementById(\'galleryFile\').click()">+ Add event photos</button>':'')+
  '<p class="small-note">You can choose multiple JPEG, PNG or WebP photos. Each upload is saved independently.</p>'+
  '<div class="experience-moderation">'+(pending.length?pending.map(p=>'<div><img src="'+img(p)+'" alt=""><div><strong>'+h(p.submitted_by||'Guest')+'</strong><p>'+h(p.caption||'No caption')+'</p>'+
    (x.can_edit?'<button class="btn tiny primary" onclick="galleryModerate(\''+p.id+'\',\'approved\')">Approve</button><button class="btn tiny" onclick="galleryModerate(\''+p.id+'\',\'rejected\')">Reject</button>':'')+'</div></div>').join(''):'<p class="small-note">No uploads awaiting review.</p>')+'</div></section></div>'+
  '<section class="card pad m-top"><div class="experience-heading"><h2>Event photo collection</h2><span>'+photos.length+' photos</span></div>'+
  '<div class="experience-photo-grid">'+photos.map(p=>'<div class="experience-photo"><button type="button" onclick="galleryOpenPhoto(\''+p.id+'\')" title="View full image"><img loading="lazy" src="'+img(p)+'" alt="'+h(p.caption||'Event photo')+'"></button>'+
  '<div><span class="exp-status '+h(p.status)+'">'+h(p.status)+'</span><strong>'+h(p.submitted_by||'Organizer')+'</strong><p>'+h(p.caption||'')+'</p>'+
  (x.can_edit?'<select aria-label="Photo status" onchange="galleryModerate(\''+p.id+'\',this.value)"><option value="pending" '+(p.status==='pending'?'selected':'')+'>Pending</option><option value="approved" '+(p.status==='approved'?'selected':'')+'>Approved</option><option value="rejected" '+(p.status==='rejected'?'selected':'')+'>Rejected</option></select><button class="btn tiny danger" onclick="galleryDelete(\''+p.id+'\')">Delete</button>':'')+'</div></div>').join('')+'</div>'+
  (!photos.length?'<p class="small-note">No photos yet. Upload your first event photo, or share the gallery link with guests.</p>':'')+'</section>';
 };
 async function refresh(){state.galleryData=await api(url());render()}
 async function update(task,success){try{await task();if(success)toast(success);await refresh()}catch(err){toast(err.message)}}
 window.gallerySetting=(key,value)=>update(()=>api(url(),{method:'PATCH',body:{[key]:value}}),'Gallery settings updated');
 window.gallerySaveTitle=()=>{const title=document.getElementById('galleryTitle')?.value||'';update(()=>api(url(),{method:'PATCH',body:{title}}),'Gallery title saved')};
 window.galleryRotate=()=>{if(!confirm('Replace the share link? Existing QR codes and links will stop working.'))return;update(()=>api(url()+'/rotate-link',{method:'POST'}),'Guest link updated')};
 window.galleryOpenPhoto=id=>window.open('/api'+url()+'/photos/'+encodeURIComponent(id)+'/full','_blank','noopener,noreferrer');
 window.galleryModerate=(id,status)=>update(()=>api(url()+'/photos/'+id,{method:'PATCH',body:{status}}),'Photo status updated');
 window.galleryDelete=id=>{if(confirm('Permanently delete this photo?'))update(()=>api(url()+'/photos/'+id,{method:'DELETE'}),'Photo deleted')};
 window.galleryUploadFiles=async files=>{
  let count=0,fail=0;for(const f of [...files].slice(0,20)){
   if(f.size>8*1024*1024){fail++;toast(f.name+' is too large');continue}
   const raw=new Uint8Array(await f.arrayBuffer()),parts=[];
   for(let i=0;i<raw.length;i+=32768)parts.push(String.fromCharCode(...raw.subarray(i,i+32768)));
   const data_base64=btoa(parts.join(''));
   try{await api(url()+'/photos',{method:'POST',body:{filename:f.name,content_type:f.type,data_base64,caption:'',submitted_by:''}});count++}
   catch(e){fail++;toast(e.message)}
  }
  await refresh();toast(count+' photos uploaded'+(fail?' · '+fail+' failed':''));
 };
 window.copyExperienceLink=async path=>{try{await navigator.clipboard.writeText(location.origin+path);toast('Link copied')}catch{toast('Unable to copy automatically')}};
 let autoUpdating=false;
 setInterval(async()=>{
  if(autoUpdating||document.hidden||state.page!=='gallery'||!state.eventId||document.activeElement?.matches('input,textarea,select')||document.querySelector('#modalRoot')?.children.length)return;
  autoUpdating=true;
  try{
   const updated=await api(url());
   if(JSON.stringify(updated.photos)!==JSON.stringify(d().photos)){
    state.galleryData=updated;render();
   }
  }catch(e){/* Manual refresh surfaces access errors. */}
  finally{autoUpdating=false}
 },12000);
 window.EventOSGallery={render:gallery,refresh};
})();
