/* PIN-authenticated customer portal: proposals, agreement acknowledgment, invoices. */
'use strict';
(()=>{
const root=document.getElementById('agencyClient');
const token=location.pathname.split('/').pop();
const path='/api/client/'+encodeURIComponent(token);
const h=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=(v,c)=>new Intl.NumberFormat('en-CA',{style:'currency',currency:c||'CAD'}).format((v||0)/100);
const label={proposal:'Proposal',contract:'Agreement',invoice:'Invoice'};
const labels={sent:'Awaiting your response',issued:'Invoice issued',accepted:'Accepted',declined:'Declined',signed:'Signed',void:'Void',paid:'Paid',partially_paid:'Partially paid'};
let info={},data=null,project=null;
async function request(url,method='GET',body){
 const res=await fetch(path+url,{method,credentials:'same-origin',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});
 const v=await res.json().catch(()=>({}));
 if(!res.ok)throw Error(res.status===429?'Too many PIN attempts. Please try again after 15 minutes.':v.detail||'Your request could not be completed');
 return v;
}
function baseHeader(title='Welcome back'){
  const brand=info.brand||data?.brand||null;
  if(brand){document.documentElement.style.setProperty('--pine',brand.primary_color);document.documentElement.style.setProperty('--ink',brand.primary_color);document.documentElement.style.setProperty('--brand-accent',brand.accent_color);document.title=(brand.name||'Your event')+' · Client portal';}
  const logo=brand?.logo_url?'<img src="'+h(brand.logo_url)+'" alt="" class="portal-brand-logo">':'✳';
  const brandName=brand?.name||'Honestly Zen Event Planning';
 return '<header class="portal-header"><a class="portal-mark" href="#top">'+logo+' <strong>'+h(brandName)+'</strong></a>'+ 
 '<div class="portal-top-label">PRIVATE CLIENT SPACE</div>'+(data?'<button class="portal-logout" id="portalLogout">Sign out ↗</button>':'')+'</header>'+
 '<section class="portal-hero" id="top"><span>AN EVENT, BEAUTIFULLY PLANNED</span><h1>'+h(title)+'</h1><p>'+h(info.event_name||data?.event?.name||'Everything for your special celebration')+'</p></section>';
}
function loginPage(message=''){
 root.innerHTML=baseHeader('Your event. Your space.')+'<section class="portal-auth"><div class="portal-auth-icon">✦</div><h2>Enter your private PIN</h2>'+
 '<p>Hello '+h(info.client_name||'there')+'. Review your documents, approvals and invoices, all in one place.</p>'+
 '<form id="portalLoginForm"><label for="portalPIN">Your 6–12 digit client access PIN</label><input id="portalPIN" name="pin" type="password" required inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6,12}" minlength="6" maxlength="12" placeholder="Enter client PIN">'+
 '<button type="submit">Open my portal ↗</button><div class="portal-message" role="status">'+h(message)+'</div></form>'+
 '<p class="portal-auth-note">Your planner shared this PIN separately from the link. Contact them if you need access reset.</p></section>'+foot();
}
function foot(){return '<footer class="portal-foot">'+h(info.brand?.footer_text||data?.brand?.footer_text||'Your private client space · Client access is private and event-specific.')+'</footer>'}
function ledger(doc){
 return '<div class="portal-ledger"><div><span>Amount</span><strong>'+money(doc.total_cents,doc.subtotal_currency)+'</strong></div>'+
 (doc.kind==='invoice'?'<div><span>Recorded payments</span><strong>'+money(doc.paid_cents,doc.subtotal_currency)+'</strong></div>'+
 '<div class="portal-balance"><span>Balance due</span><strong>'+money(doc.balance_cents,doc.subtotal_currency)+'</strong></div>':'')+'</div>';
}
function docHtml(doc){
 return '<article class="portal-document" id="portal-doc-'+h(doc.id)+'"><header><div><small>'+h(doc.number)+' · '+h(label[doc.kind])+'</small><h3>'+h(doc.title)+'</h3></div><span class="portal-status">'+h(labels[doc.display_status]||doc.display_status)+'</span></header>'+
 '<p>'+h(doc.body).replace(/\n/g,'<br>')+'</p>'+
 (doc.lines.length?'<div class="portal-table-scroll"><table><thead><tr><th>Service</th><th>Quantity</th><th>Unit price</th><th>Total</th></tr></thead><tbody>'+
 doc.lines.map(l=>'<tr><td>'+h(l.description)+'</td><td>'+l.quantity+'</td><td>'+money(l.unit_price_cents,doc.subtotal_currency)+'</td><td>'+money(l.quantity*l.unit_price_cents,doc.subtotal_currency)+'</td></tr>').join('')+'</tbody></table></div>':'')+
 '<div class="portal-finance-row"><span>Discount '+money(doc.discount_cents,doc.subtotal_currency)+'</span><span>Tax '+(doc.tax_bps/100).toFixed(2)+'%</span></div>'+
 ledger(doc)+
 (doc.terms?'<details class="portal-terms" open><summary>Terms & conditions</summary><p>'+h(doc.terms).replace(/\n/g,'<br>')+'</p></details>':'')+
 (doc.valid_until?'<p class="portal-fine">Valid until '+h(doc.valid_until)+'</p>':'')+
 (doc.kind==='invoice'?'<section class="portal-company"><strong>Issued by '+h(data.company?.legal_name||'Your event planner')+'</strong><p>'+h(data.company?.billing_address||'').replace(/\n/g,'<br>')+'</p><p>'+h(data.company?.billing_email||'')+' '+h(data.company?.billing_phone||'')+'</p>'+(data.company?.tax_number?'<p>Tax ID: '+h(data.company.tax_number)+'</p>':'')+'<strong>Payment instructions</strong><p>'+h(data.company?.payment_instructions||'Contact your planner directly for payment details.')+'</p></section>':'')+
 (doc.kind==='invoice'&&doc.due_date?'<p class="portal-fine">Payment due '+h(doc.due_date)+'. '+(project?.stripe_invoice_enabled?'Secure payment is available after signing into your client portal.':'Contact your planner for payment instructions.')+'</p>':'')+
 (doc.signer_name?'<div class="portal-signed">✓ Acknowledged by '+h(doc.signer_name)+' on '+h(doc.responded_at.slice(0,10))+'</div>':'')+
 '<div class="portal-actions">'+(doc.kind==='proposal'&&doc.status==='sent'?'<button data-response="accept" data-id="'+h(doc.id)+'">Accept proposal</button><button class="portal-outline" data-response="decline" data-id="'+h(doc.id)+'">Decline</button>':'')+
 (doc.kind==='contract'&&doc.status==='sent'?'<button data-response="sign" data-id="'+h(doc.id)+'">Review & acknowledge agreement</button>':'')+
 (doc.kind==='invoice'&&doc.status==='issued'&&doc.balance_cents>0&&project?.stripe_invoice_enabled?
   '<button data-invoice-checkout="'+h(doc.id)+'">Pay invoice securely</button>':
 doc.kind==='invoice'&&doc.status==='issued'&&doc.balance_cents>0&&project?.stripe_test_enabled?
   '<button data-test-checkout="'+h(doc.id)+'">Pay with Stripe TEST card</button>':'')+
 '<button class="portal-outline" data-print="'+h(doc.id)+'">Print document</button></div></article>';
}
function dashboard(){
 if(!data)return loginPage();
 const docs=data.documents||[];
 info.event_name=data.event.name;
 root.innerHTML=baseHeader('Hello, '+(data.client_name||'friend')+'.')+
 '<section class="portal-intro"><span class="portal-eyebrow">YOUR CLIENT DASHBOARD</span><h2>Everything in its place.</h2><p>Review your planning documents, respond to proposals and agreements, and keep track of invoices and recorded balances.</p>'+
 '<div class="portal-stats">'+['proposal','contract','invoice'].map(k=>'<div><strong>'+docs.filter(x=>x.kind===k).length+'</strong><span>'+h(k==='contract'?'Agreements':k==='invoice'?'Invoices':'Proposals')+'</span></div>').join('')+'</div></section>'+
 '<section class="portal-documents portal-project-milestones"><div class="portal-section-heading"><h2>Project progress</h2><span>'+((project?.milestones||[]).length)+' shared milestones</span></div>'+
 (project?.milestones?.length?project.milestones.map(m=>
  '<article class="portal-project-line"><div><span class="portal-update-dot"></span><strong>'+h(m.title)+'</strong>'+
  '<small>'+h(m.due_date||'Date not set')+' · '+h(m.status.replaceAll('_',' '))+'</small>'+
  (m.details?'<p>'+h(m.details)+'</p>':'')+'</div></article>').join(''):
  '<div class="portal-empty"><strong>Project updates will appear here.</strong><p>Your planner shares selected milestones as work progresses.</p></div>')+
 '</section>'+
 (project?.stripe_test_enabled?'<p class="portal-test-warning">Stripe TEST mode only — no real charges. An invoice is credited only after Stripe sends a verified payment notification.</p>':'')+
 '<section class="portal-documents"><div class="portal-section-heading"><h2>Your documents</h2><span>'+docs.length+' shared</span></div>'+
 (docs.length?docs.map(docHtml).join(''):'<div class="portal-empty"><strong>Nothing to review yet</strong><p>Your planner will share proposals, agreements and invoices here as they are ready.</p></div>')+'</section>'+foot();
}
function askName(doc,decision){
 const isSign=decision==='sign';
 const title=isSign?'Review & acknowledge agreement':decision==='accept'?'Accept this proposal':'Decline this proposal';
 const modal=document.createElement('div');modal.className='portal-modal';modal.innerHTML='<div class="portal-dialog" role="dialog" aria-modal="true"><button data-close-dialog aria-label="Close" class="portal-dialog-close">×</button>'+
 '<h2>'+title+'</h2><p>'+h(doc.title)+'</p>'+
 (isSign?'<p class="portal-sign-warning">By submitting, you acknowledge that you have read and agree to the agreement terms. Your typed name, timestamp and a document fingerprint are recorded. This is not a certified third-party digital-signature service.</p>':'')+
 '<form id="portalResponseForm"><label>Your full legal name<input name="signer_name" maxlength="150" required autocomplete="name" placeholder="Type your full name"></label>'+
 (isSign?'<label class="portal-consent"><input name="consent" type="checkbox" required> I have reviewed this agreement and explicitly consent to its terms and electronic acknowledgment.</label>':'')+
 '<p id="portalResponseError" role="status"></p><button type="submit">Confirm '+title.toLowerCase()+'</button></form></div>';
 document.body.appendChild(modal);
 modal.querySelector('[data-close-dialog]').addEventListener('click',()=>modal.remove());
 modal.querySelector('form').addEventListener('submit',async e=>{
  e.preventDefault();const f=new FormData(e.target),button=e.target.querySelector('button');button.disabled=true;
  try{
   await request('/documents/'+encodeURIComponent(doc.id)+'/respond','POST',
    {decision,signer_name:f.get('signer_name'),consent:f.get('consent')==='on'});
   modal.remove();await reload();
  }catch(err){modal.querySelector('#portalResponseError').textContent=err.message;button.disabled=false}
 });
}
function printDoc(id){
 const doc=data.documents.find(d=>d.id===id);if(!doc)return;
 const content='<div style="font:14px Arial;margin:45px;line-height:1.7;color:#183f34"><h1>'+h(doc.number)+' · '+h(doc.title)+'</h1><p>'+h(data.event.name)+'</p><p>'+h(doc.body)+'</p>'+
 '<table style="width:100%;border-collapse:collapse">'+doc.lines.map(l=>'<tr><td style="padding:12px;border-bottom:1px solid #ccc">'+h(l.description)+'</td><td>'+l.quantity+'</td><td>'+money(l.quantity*l.unit_price_cents,doc.subtotal_currency)+'</td></tr>').join('')+'</table>'+
 '<h2>Total '+money(doc.total_cents,doc.subtotal_currency)+'</h2>'+(doc.kind==='invoice'?'<p>Issued by '+h(data.company?.legal_name||'Your event planner')+'</p><p>'+h(data.company?.billing_address||'')+'</p><p>Tax ID: '+h(data.company?.tax_number||'')+'</p><p>Payment instructions: '+h(data.company?.payment_instructions||'Contact your event planner')+'</p>':'')+
 (doc.kind==='invoice'?'<p>Balance due '+money(doc.balance_cents,doc.subtotal_currency)+'</p>':'')+
 '<p>'+h(doc.terms).replace(/\n/g,'<br>')+'</p>'+
 (doc.signer_name?'<p>Acknowledged by '+h(doc.signer_name)+' on '+h(doc.responded_at)+'</p>':'')+'</div>';
 const iframe=document.createElement('iframe');iframe.style.position='fixed';iframe.style.width='0';iframe.style.height='0';iframe.style.border='0';iframe.setAttribute('title','Printable client document');
 document.body.appendChild(iframe);
 const page=iframe.contentDocument;page.open();page.write('<!doctype html><html><head><title>'+h(doc.number)+'</title></head><body>'+content+'</body></html>');page.close();
 setTimeout(()=>{iframe.contentWindow?.focus();iframe.contentWindow?.print()},180);
 setTimeout(()=>iframe.remove(),120000);
}
async function reload(){
 try{data=await request('/dashboard');if(data.brand)info.brand=data.brand;project=await request('/project').catch(()=>null);dashboard()}
 catch(err){if(/PIN|expired/i.test(err.message)){data=null;loginPage()}else{root.innerHTML='<p class="portal-error">'+h(err.message)+'</p>'}}
}
root.addEventListener('submit',async e=>{
 if(e.target.id!=='portalLoginForm')return;
 e.preventDefault();const pin=e.target.elements.pin.value,btn=e.target.querySelector('button');btn.disabled=true;
 try{await request('/login','POST',{pin});await reload()}
 catch(err){root.querySelector('.portal-message').textContent=err.message;btn.disabled=false}
});
root.addEventListener('click',e=>{
 if(e.target.id==='portalLogout'){request('/logout','POST',{}).finally(()=>{data=null;loginPage()})}
 const action=e.target.closest('[data-response]');
 if(action&&data){const doc=data.documents.find(d=>d.id===action.dataset.id);if(doc)askName(doc,action.dataset.response)}
 const print=e.target.closest('[data-print]');if(print&&data)printDoc(print.dataset.print);
 const invoicePay=e.target.closest('[data-invoice-checkout]');
 if(invoicePay&&data&&project?.stripe_invoice_enabled){
  invoicePay.disabled=true;
  request('/documents/'+encodeURIComponent(invoicePay.dataset.invoiceCheckout)+'/checkout','POST',{})
   .then(result=>{if(!/^https:\/\/checkout\.stripe\.com\//.test(result.url||''))throw Error('Invalid Stripe Checkout URL');location.assign(result.url)})
   .catch(err=>{invoicePay.disabled=false;alert(err.message)});
 }
 const pay=e.target.closest('[data-test-checkout]');
 if(pay&&data&&project?.stripe_test_enabled){
  pay.disabled=true;
  request('/documents/'+encodeURIComponent(pay.dataset.testCheckout)+'/test-checkout','POST',{})
   .then(result=>{
    if(!result.test_mode||!/^https:\/\/checkout\.stripe\.com\//.test(result.url||''))throw Error('Invalid Stripe test checkout URL');
    window.location.assign(result.url);
   }).catch(err=>{pay.disabled=false;alert(err.message)});
 }
});
async function start(){
 try{
  info=await request('/session');
  if(info.authenticated)await reload();else loginPage();
 }catch(err){root.innerHTML='<div class="portal-error"><h2>Client portal unavailable</h2><p>'+h(err.message)+'</p></div>'}
}
start();
})();