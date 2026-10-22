/* Public Honestly Zen Event Planning event microsite: only published data, private RSVP tokens. */
'use strict';
(()=>{
const root=document.getElementById('eventSiteRoot');
const parts=location.pathname.split('/').filter(Boolean),slug=parts[1],token=parts[2];
const preview=new URLSearchParams(location.search).get('preview')==='1';
const api='/api/event-site/'+encodeURIComponent(slug)+'/'+encodeURIComponent(token);
const viewQuery=preview?'?preview=true':'';
const h=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dates=s=>{if(!s)return'';const d=new Date(s.length===10?s+'T12:00:00':s);return Number.isFinite(d.getTime())?new Intl.DateTimeFormat('en-US',{month:'long',day:'numeric',year:'numeric'}).format(d):String(s)};
const time=s=>{if(!s)return'';const d=new Date(s);return Number.isFinite(d.getTime())?new Intl.DateTimeFormat('en-US',{hour:'numeric',minute:'2-digit'}).format(d):String(s)};
const urlsafe=x=>/^https:\/\/[^\s"'<>\\]+$/i.test(x||'');
const mapLink=s=>'https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(s);
let info;
function countdown(){
 const target=info?.event?.start_date;if(!target)return;
 const d=new Date(target.length===10?target+'T12:00:00':target);
 if(!Number.isFinite(d.getTime()))return;
 const diff=d.getTime()-Date.now();
 const day=Math.max(0,Math.ceil(diff/86400000));
 const el=document.getElementById('siteCountdown');
 if(el)el.textContent=diff>0?day+' '+(day===1?'day':'days')+' until our celebration':'The celebration has arrived';
}
const section=(title,eyebrow,body,id)=>'<section id="'+id+'" class="e-section"><div class="e-section-inner"><div class="e-eyebrow">'+eyebrow+'</div><h2>'+h(title)+'</h2>'+body+'</div></section>';
function render(){
 const e=info.event,c=info.content;
 document.body.dataset.theme=c.template;
 const brand=info.brand||null;
 document.documentElement.style.setProperty('--site-accent',brand?.accent_color||c.accent);
 if(brand)document.documentElement.style.setProperty('--brand-primary',brand.primary_color);
 document.body.classList.toggle('brand-site',!!brand);
 document.title=(c.title||e.name)+' | Celebration Website';
 const heading=c.title||e.name;
 const order=c.section_order||[];
 const site={
 story:()=>section(c.story_title||'Our story','THE STORY',
   '<div class="e-prose">'+h(c.story||c.welcome).replace(/\n/g,'<br>')+'</div>','story'),
 schedule:()=>section('Order of celebration','THE DAY, TOGETHER',
  info.schedule.length?'<div class="e-timeline">'+info.schedule.map(s=>'<article class="e-timeline-row"><div class="e-timeline-time">'+h(dates(s.start_at))+'<br><strong>'+h(time(s.start_at))+'</strong></div><div><h3>'+h(s.title)+'</h3>'+(s.location?'<p>'+h(s.location)+'</p>':'')+(s.end_at?'<small>Until '+h(time(s.end_at))+'</small>':'')+'</div></article>').join('')+'</div>':'<p>We’ll share the celebration schedule here soon.</p>','schedule'),
 venue:()=>section('The place to be','FIND YOUR WAY',
  '<div class="e-venue"><div class="e-venue-art" aria-hidden="true">✦</div><div><h3>'+h(e.location||'Our special venue')+'</h3><p>'+h(c.parking||'We look forward to welcoming you.')+'</p>'+(c.dress_code?'<p><strong>Dress code:</strong> '+h(c.dress_code)+'</p>':'')+(c.travel?'<p><strong>Travel information:</strong> '+h(c.travel).replace(/\n/g,'<br>')+'</p>':'')+(c.contact?'<p><strong>Questions?</strong> '+h(c.contact)+'</p>':'')+(e.location?'<a class="e-link" target="_blank" rel="noopener noreferrer" href="'+mapLink(e.location)+'">Get directions ↗</a>':'')+'</div></div>','venue'),
 stay:()=>section('Stay & travel','MAKE YOURSELF AT HOME',
  c.accommodations?.length?'<div class="e-hotel-grid">'+c.accommodations.map(x=>'<article class="e-hotel"><h3>'+h(x.name)+'</h3><p>'+h(x.address)+'</p><p>'+h(x.note)+'</p>'+(urlsafe(x.url)?'<a class="e-link" rel="noopener noreferrer" target="_blank" href="'+h(x.url)+'">View accommodation ↗</a>':'')+'</article>').join('')+'</div>':'<p>Travel and accommodation recommendations will be shared soon.</p>','stay'),
 faq:()=>section('A few things to know','GOOD TO KNOW',
  c.faq?.length?'<div class="e-faq">'+c.faq.map(x=>'<details><summary>'+h(x.question)+'<span>+</span></summary><p>'+h(x.answer).replace(/\n/g,'<br>')+'</p></details>').join('')+'</div>':'<p>For any questions, please reach out to the event organizers.</p>','faq'),
 rsvp:()=>section('Kindly RSVP','SAVE YOUR SEAT',
  '<p>Your invitation has a private RSVP code. Enter it below to open the response form made just for you.</p><form id="eRsvpForm" class="e-rsvp"><label for="eRsvpCode">Personal invitation code</label><div><input id="eRsvpCode" name="invitation_token" type="text" maxlength="180" placeholder="Paste your invitation code" required autocomplete="off"><button class="e-btn" type="submit">Open my RSVP ↗</button></div><p id="eRsvpError" role="status" class="e-form-status"></p></form><p class="e-fineprint">You can also use the personal RSVP link sent directly by your host.</p>','rsvp'),
 gallery:()=>info.gallery_url?section('The moments we share','LIVE MEMORIES',
  '<p>Capture the magic together. Browse the event photo gallery and share your favorite moments.</p><a class="e-btn" href="'+h(info.gallery_url)+'">Explore photo gallery ↗</a>','gallery'):'',
 gifts:()=>info.registry_url?section('Your presence is our gift','OUR REGISTRY',
  '<p>For those who have asked, we have created a thoughtful wishlist and gift registry.</p><a class="e-btn" href="'+h(info.registry_url)+'">View gift registry ↗</a>','gifts'):''
 };
 const navigation=order.filter(x=>c.sections?.[x]&&(x!=='gallery'||info.gallery_url)&&(x!=='gifts'||info.registry_url));
 root.className='';
 root.innerHTML='<header class="e-header"><a href="#top" class="e-wordmark">'+(brand?.logo_url?'<img class="e-brand-logo" alt="" src="'+h(brand.logo_url)+'">':'✦')+' <span>'+h(brand?.name||heading)+'</span></a><nav aria-label="Event website sections">'+navigation.slice(0,5).map(x=>'<a href="#'+x+'">'+h(({story:'Our story',schedule:'Schedule',venue:'Venue',stay:'Stay',faq:'FAQ',rsvp:'RSVP',gallery:'Photos',gifts:'Gifts'})[x])+'</a>').join('')+'</nav>'+(c.sections?.rsvp?'<a class="e-top-cta" href="#rsvp">RSVP ↗</a>':'')+'</header>'+
 '<main id="top"><section class="e-hero '+(info.has_cover?'e-has-cover':'')+'">'+(info.has_cover?'<div class="e-cover" style="background-image:url(&quot;'+api+'/cover'+viewQuery+'&quot;)"></div>':'<div class="e-ornament" aria-hidden="true">❋</div>')+
 '<div class="e-hero-inner"><div class="e-small-label">YOU ARE INVITED TO CELEBRATE</div><h1>'+h(heading)+'</h1>'+(c.subtitle?'<p class="e-subtitle">'+h(c.subtitle)+'</p>':'')+
 '<div class="e-event-date">'+h(dates(e.start_date))+'</div><div class="e-hero-location">'+h(e.location||'Location to be announced')+'</div><p id="siteCountdown" class="e-countdown"></p>'+(c.sections?.rsvp?'<a class="e-btn e-hero-button" href="#rsvp">Celebrate with us ↗</a>':'')+'</div></section>'+
 '<div class="e-intro"><span>✦</span><p>'+h(c.welcome).replace(/\n/g,'<br>')+'</p><span>✦</span></div>'+
 navigation.map(x=>site[x]?.()||'').join('')+
 '<section class="e-ending"><div class="e-ending-flower">❋</div><h2>With love, and a little magic.</h2><p>'+h(c.footer)+'</p><a href="#top">Back to the beginning ↑</a></section></main>'+
 '<footer class="e-footer"><span>'+h(brand?.name||heading)+'</span><small>'+h(brand?.footer_text||'Created with Honestly Zen Event Planning')+'</small></footer>';
 countdown();
}
async function load(){
 try{
  const r=await fetch(api+viewQuery,{credentials:'same-origin',cache:'no-store'});
  const json=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(typeof json.detail==='string'?json.detail:'Website unavailable');
  info=json;render();
 }catch(e){root.className='site-unavailable';root.innerHTML='<h1>Website unavailable</h1><p>'+h(e.message)+'</p><a href="/">Return home</a>'}
}
root.addEventListener('submit',async e=>{
 if(e.target.id!=='eRsvpForm')return;
 e.preventDefault();
 const field=e.target.elements.invitation_token;
 const raw=field.value.trim();
 const tokenMatch=raw.match(/(?:\/rsvp\/|\/api\/rsvp\/)([A-Za-z0-9_-]+)/);
 const invitation_token=tokenMatch?tokenMatch[1]:raw;
 const status=e.target.querySelector('#eRsvpError'),btn=e.target.querySelector('button[type=submit]');
 btn.disabled=true;status.textContent='';
 try{
  const r=await fetch(api+'/rsvp-link',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({invitation_token})});
  const d=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'The invitation code was not recognized.');
  location.href=d.rsvp_url;
 }catch(err){status.textContent=err.message;btn.disabled=false}
});
load();
setInterval(()=>{if(info&&!document.hidden)countdown()},60000);
})();