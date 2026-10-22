/* Phase 9 Honestly Zen Event Planning organizer agency desk. */
'use strict';
(()=>{
const s=()=>state.agencyData||{documents:[]};
const base=()=>evpath()+'/agency';
const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=(v,c)=>new Intl.NumberFormat('en-CA',{style:'currency',currency:c||s().currency||'CAD'}).format((v||0)/100);
const kinds={proposal:'Proposal',contract:'Contract',invoice:'Invoice'};
let filter='all',lines=[];
const input=(title,name,value='',type='text')=>'<label class="field"><span>'+h(title)+'</span><input type="'+type+'" name="'+name+'" value="'+h(value)+'"></label>';
const area=(title,name,value='')=>'<label class="field wide"><span>'+h(title)+'</span><textarea name="'+name+'" rows="4">'+h(value)+'</textarea></label>';
const btn=(label,action,id='',primary=false)=>'<button class="btn tiny '+(primary?'primary':'')+'" data-agency-action="'+action+'" data-id="'+h(id)+'" type="button">'+h(label)+'</button>';
const status=v=>'<span class="agency-status">'+h(v.replaceAll('_',' '))+'</span>';
function render(){
 const d=s(),docs=d.documents||[],portal=d.portal;
 const shown=docs.filter(doc=>filter==='all'||doc.kind===filter);
 const counts={proposal:docs.filter(x=>x.kind==='proposal').length,contract:docs.filter(x=>x.kind==='contract').length,invoice:docs.filter(x=>x.kind==='invoice').length};
 return head('Agency client desk','Manage client proposals, contract acknowledgments and invoices for this event.')+
 '<div class="agency-metrics">'+[['Proposals',counts.proposal],['Agreements',counts.contract],['Invoices',counts.invoice],['Balance due',money(d.receivable_cents)]].map(x=>'<div class="card"><small>'+x[0]+'</small><strong>'+x[1]+'</strong></div>').join('')+'</div>'+
 '<section class="card agency-portal agency-company"><div><small>YOUR AGENCY BUSINESS PROFILE</small><h2>'+h(d.company?.legal_name||state.ws.name)+'</h2><p>'+h(d.company?.billing_address||'Add your billing address, tax identifier and payment instructions to appear on client invoices.')+'</p></div><div class="agency-buttons">'+(d.can_edit?btn('Edit billing details','company-edit'):'')+'</div></section>'+
 '<section class="card agency-portal"><div><small>PRIVATE CLIENT PORTAL</small><h2>'+h(portal?.client_name||'Invite your client')+'</h2>'+
 '<p>'+h(portal?'Client access uses a private link plus a separate 6–12 digit PIN.':'Give clients a restricted view of their own proposals, agreements and invoices.')+'</p>'+
 (portal?'<div class="agency-link"><input readonly aria-label="Client portal link" value="'+h(location.origin+portal.url)+'">'+btn('Copy link','copy')+'</div><small>Portal '+(portal.active?'active':'disabled')+' · Expires '+h(portal.expires_at.slice(0,10))+'</small>':'')+'</div>'+
 '<div class="agency-buttons">'+(d.can_edit?(portal?btn('Manage access & PIN','portal-edit')+btn('Email portal link','portal-email'):btn('Create client portal','portal-create','',true)):'')+
 (portal?btn('Open client view','portal-open'):'')+'</div></section>'+
 '<section class="agency-doc-heading"><div><h2>Proposals, agreements & invoices</h2><p>Draft documents stay private until issued. Issued versions are locked.</p></div>'+
 (d.can_edit?'<div class="agency-buttons">'+btn('New proposal','new-proposal')+btn('New contract','new-contract')+btn('New invoice','new-invoice','',true)+'</div>':'')+'</section>'+
 '<div class="agency-filters">'+[['all','All'],['proposal','Proposals'],['contract','Contracts'],['invoice','Invoices']].map(x=>'<button data-agency-filter="'+x[0]+'" class="'+(filter===x[0]?'active':'')+'">'+x[1]+'</button>').join('')+
 '<a href="/api'+base()+'/invoices.csv" class="btn tiny">Export invoices CSV ↗</a></div>'+
 '<div class="agency-grid">'+(shown.length?shown.map(doc=>'<article class="card agency-doc"><div class="agency-doc-top"><small>'+h(doc.number)+' · '+kinds[doc.kind]+'</small>'+status(doc.display_status)+'</div><h3>'+h(doc.title)+'</h3><p>'+h(doc.body?.slice(0,180)||'No additional description')+'</p>'+
 '<div class="agency-doc-amount"><strong>'+money(doc.total_cents,doc.subtotal_currency)+'</strong>'+(doc.kind==='invoice'?'<small>Outstanding '+money(doc.balance_cents,doc.subtotal_currency)+'</small>':'')+'</div>'+
 '<div class="agency-buttons">'+btn('View','view',doc.id)+(d.can_edit?(
 doc.status==='draft'?btn('Edit','edit',doc.id)+btn('Issue','issue',doc.id,true)+btn('Delete','delete',doc.id):
 doc.kind==='proposal'&&doc.status==='accepted'?btn('Make invoice','invoice',doc.id):
 doc.kind==='invoice'&&doc.status==='issued'?
   (doc.balance_cents>0?btn('Record payment','payment',doc.id):'')+(!doc.paid_cents?btn('Void','void',doc.id):''):''):'')+
   (d.can_edit&&doc.status!=='draft'?btn('Email notice','notify',doc.id):'')+'</div></article>').join(''):
 '<div class="card agency-empty"><h3>No client documents yet</h3><p>Start by preparing a proposal, then add a contract and an invoice.</p></div>')+'</div>'+
 (window.EventOSAgencyAdvanced?.render()||'')+
 '<section class="card pad m-top"><h3>Client activity</h3>'+(d.history?.length?d.history.slice(0,15).map(x=>'<div class="attention-row"><span>'+h(x.action.replaceAll('.',' · '))+'</span><small>'+h(x.created_at.slice(0,16).replace('T',' '))+'</small></div>').join(''):'<p class="small-note">Responses and agency changes will appear here.</p>')+'</section>';
}
function modal(title,html,onSubmit){
 const root=document.getElementById('modalRoot');
 root.innerHTML='<div class="modal-overlay"><section class="modal agency-modal" role="dialog" aria-modal="true" aria-label="'+h(title)+'"><header class="modal-head"><h2>'+h(title)+'</h2><button class="xclose" data-agency-close>×</button></header><div class="modal-body">'+html+'</div></section></div>';
 root.querySelector('#agencyForm')?.addEventListener('submit',onSubmit);
}
const close=()=>document.getElementById('modalRoot').replaceChildren();
async function callApi(fn,label){
 try{await fn();close();[state.agencyData,state.agencyAdvanced]=await Promise.all([api(base()),api(base()+'/advanced')]);window.render();toast(label)}
 catch(err){toast(err.message)}
}
function editCompany(){
 const c=s().company||{};
 modal('Agency billing details','<form id="agencyForm"><div class="inline-fields">'+
 input('Legal business name','legal_name',c.legal_name||state.ws.name)+
 input('Billing email','billing_email',c.billing_email||'','email')+
 input('Billing phone','billing_phone',c.billing_phone||'')+
 input('Tax registration / ID','tax_number',c.tax_number||'')+
 area('Billing address','billing_address',c.billing_address||'')+
 area('Payment instructions for invoices','payment_instructions',c.payment_instructions||'')+
 '</div><p class="small-note">Enter your agency details exactly as they should appear on client invoices. Keep sensitive bank credentials out of public documents.</p>'+
 '<div class="modal-actions"><button type="button" class="btn" data-agency-close>Cancel</button><button type="submit" class="btn primary">Save billing profile</button></div></form>',
 e=>{
  e.preventDefault();const f=new FormData(e.target);
  callApi(()=>api(prefix()+'/agency/profile',{method:'PUT',body:{
   legal_name:f.get('legal_name'),billing_email:f.get('billing_email'),billing_phone:f.get('billing_phone'),
   tax_number:f.get('tax_number'),billing_address:f.get('billing_address'),
   payment_instructions:f.get('payment_instructions')}}),'Agency billing profile saved');
 });
}
function editPortal(isEdit){
 const p=s().portal;
 const html='<form id="agencyForm"><div class="inline-fields">'+
 input('Client name','client_name',p?.client_name||'')+input('Client email','client_email',p?.client_email||'','email')+
 input(isEdit?'New PIN (optional)':'PIN (6–12 numbers)','pin','')+
 (isEdit?'<label class="field"><span>Portal access</span><select name="active"><option value="true" '+(p.active?'selected':'')+'>Enabled</option><option value="false" '+(!p.active?'selected':'')+'>Disabled</option></select></label>'+
 '<label class="field"><span>Extend access</span><select name="extend_days"><option value="">No change</option><option value="90">90 days</option><option value="365">365 days</option><option value="730">730 days</option></select></label>'+
 '<label class="field wide"><span><input type="checkbox" name="rotate_link"> Replace the private link and revoke earlier sessions</span></label>':'')+
 '</div><p class="small-note">PINs are stored as salted hashes and cannot be recovered. Share the PIN separately from the link.</p>'+
 '<div class="modal-actions"><button type="button" class="btn" data-agency-close>Cancel</button><button class="btn primary" type="submit">Save client access</button></div></form>';
 modal(isEdit?'Edit client access':'Invite client',html,e=>{
  e.preventDefault();const f=new FormData(e.target);
  const p={client_name:f.get('client_name'),client_email:f.get('client_email')||'',pin:f.get('pin')||''};
  if(!isEdit&&!/^\d{6,12}$/.test(p.pin)){toast('PIN must contain 6 to 12 numbers');return}
  if(isEdit){
   if(!p.pin)delete p.pin;
   p.active=f.get('active')==='true';
   if(f.get('extend_days'))p.extend_days=Number(f.get('extend_days'));
   if(f.get('rotate_link'))p.rotate_link=true;
  }
  callApi(()=>api(base()+'/portal',{method:isEdit?'PATCH':'POST',body:p}),'Client portal saved');
 });
}
const defaults=kind=>({title:(kind==='proposal'?'Planning proposal':kind==='contract'?'Services agreement':'Client invoice')+' — '+state.event.name,
 body:kind==='contract'?'Describe the services and deliverables agreed with this client.':'',
 terms:kind==='contract'?'Describe payment, cancellation, responsibilities and dispute provisions before inviting signature.':'',
 lines:[{description:'Event planning services',quantity:1,unit_price_cents:0}],discount_cents:0,tax_bps:0,due_date:'',valid_until:''});
const lineHtml=()=>lines.map((l,i)=>'<div class="agency-line" data-agency-line="'+i+'">'+
 '<label>Description<input name="description" required maxlength="180" value="'+h(l.description)+'"></label>'+
 '<label>Qty<input name="quantity" type="number" min="1" max="10000" value="'+h(l.quantity)+'"></label>'+
 '<label>Unit ('+h(s().currency)+')<input name="price" type="number" min="0" step=".01" value="'+(l.unit_price_cents/100).toFixed(2)+'"></label>'+
 '<button type="button" class="btn tiny" data-remove-line="'+i+'">×</button></div>').join('');
function readLines(){
 lines=[...document.querySelectorAll('[data-agency-line]')].map(el=>({
  description:el.querySelector('[name=description]').value,quantity:Number(el.querySelector('[name=quantity]').value),
  unit_price_cents:Math.round(Number(el.querySelector('[name=price]').value)*100)}));
}
function editDoc(kind,id){
 const d=id?s().documents.find(x=>x.id===id):defaults(kind);
 lines=d.lines.map(x=>({...x}));
 modal((id?'Edit ':'New ')+kinds[kind],
 '<form id="agencyForm"><div class="inline-fields">'+
 input('Title','title',d.title)+input('Valid until','valid_until',d.valid_until||'','date')+
 (kind==='invoice'?input('Payment due','due_date',d.due_date||'','date'):'')+
 area('Description and scope','body',d.body)+area('Terms and conditions','terms',d.terms)+'</div>'+
 '<div class="agency-line-heading"><h3>Itemized services</h3><button class="btn tiny" type="button" data-add-line>+ Add line</button></div><div id="agencyLines">'+lineHtml()+'</div>'+
 '<div class="inline-fields">'+input('Discount ('+s().currency+')','discount',(d.discount_cents/100).toFixed(2),'number')+
 input('Tax (%)','tax',(d.tax_bps/100).toFixed(2),'number')+'</div>'+
 '<p class="small-note">These are estimated amounts in '+h(s().currency)+'. No online payment is collected here.</p>'+
 '<div class="modal-actions"><button class="btn" type="button" data-agency-close>Cancel</button><button class="btn primary" type="submit">Save draft</button></div></form>',
 e=>{
  e.preventDefault();readLines();const f=new FormData(e.target);
  const discount=Math.round(Number(f.get('discount')||0)*100),tax_bps=Math.round(Number(f.get('tax')||0)*100);
  if(!Number.isSafeInteger(discount)||!Number.isSafeInteger(tax_bps)||!lines.every(x=>Number.isSafeInteger(x.unit_price_cents)&&Number.isSafeInteger(x.quantity))){toast('Enter valid prices and quantities');return}
  const doc={title:f.get('title'),body:f.get('body'),terms:f.get('terms'),lines,
    discount_cents:discount,tax_bps,due_date:f.get('due_date')||'',valid_until:f.get('valid_until')||''};
  callApi(()=>api(id?base()+'/documents/'+id:base()+'/documents',{method:id?'PUT':'POST',body:id?doc:{kind,document:doc}}),'Draft saved');
 });
}
function showDoc(id){
 const d=s().documents.find(v=>v.id===id);if(!d)return;
 modal(d.number+' · '+d.title,'<div class="agency-detail"><p>'+h(d.body)+'</p><div class="agency-terms">'+h(d.terms)+'</div>'+
 '<div class="table-wrap"><table class="table"><thead><tr><th>Service</th><th>Qty</th><th>Unit</th><th>Amount</th></tr></thead><tbody>'+
 d.lines.map(l=>'<tr><td>'+h(l.description)+'</td><td>'+l.quantity+'</td><td>'+money(l.unit_price_cents,d.subtotal_currency)+'</td><td>'+money(l.quantity*l.unit_price_cents,d.subtotal_currency)+'</td></tr>').join('')+'</tbody></table></div>'+
 '<p>Discount: '+money(d.discount_cents,d.subtotal_currency)+' · Tax: '+(d.tax_bps/100).toFixed(2)+'%</p>'+
 '<h3>Total: '+money(d.total_cents,d.subtotal_currency)+'</h3>'+
 (d.kind==='invoice'?'<p>Paid '+money(d.paid_cents,d.subtotal_currency)+' · Outstanding '+money(d.balance_cents,d.subtotal_currency)+'</p>':'')+
 (d.signer_name?'<p><strong>Client response:</strong> '+h(d.signer_name)+' · '+h(d.responded_at)+'</p>':'')+
 '<div class="modal-actions"><button class="btn" data-agency-close>Close</button>'+
 (d.kind==='contract'&&d.status==='signed'?'<a class="btn" download href="/api'+base()+'/documents/'+h(id)+'/acknowledgment.pdf">↓ Evidence receipt</a>':'')+
 '<button class="btn primary" data-agency-print="'+h(id)+'">Print / save PDF</button></div></div>');
}
function paymentDialog(id){
 const d=s().documents.find(v=>v.id===id);if(!d)return;
 modal('Record payment received','<form id="agencyForm"><p>Balance '+money(d.balance_cents,d.subtotal_currency)+'</p>'+
 input('Amount ('+d.subtotal_currency+')','amount',(d.balance_cents/100).toFixed(2),'number')+
 '<label class="field"><span>Method</span><select name="method"><option>bank transfer</option><option>cash</option><option>cheque</option><option>e-transfer</option><option>other</option></select></label>'+
 input('Paid on','paid_on',new Date().toISOString().slice(0,10),'date')+area('Internal note','note','')+
 '<p class="small-note">Only record payments already received outside Honestly Zen Event Planning.</p>'+
 '<div class="modal-actions"><button class="btn" data-agency-close>Cancel</button><button class="btn primary" type="submit">Record payment</button></div></form>',e=>{
  e.preventDefault();const f=new FormData(e.target),amount_cents=Math.round(Number(f.get('amount'))*100);
  callApi(()=>api(base()+'/documents/'+id+'/payments',{method:'POST',body:{
   amount_cents,method:f.get('method'),paid_on:f.get('paid_on'),note:f.get('note')}}),'Payment recorded');
 });
}
function printDoc(id){
 const d=s().documents.find(v=>v.id===id);if(!d)return;
 const rows=d.lines.map(z=>'<tr><td>'+h(z.description)+'</td><td>'+z.quantity+'</td><td>'+money(z.unit_price_cents,d.subtotal_currency)+'</td><td>'+money(z.quantity*z.unit_price_cents,d.subtotal_currency)+'</td></tr>').join('');
 const html='<!doctype html><html><head><meta charset="utf-8"><title>'+h(d.number)+'</title><style>body{font:14px Arial;max-width:800px;margin:7vh auto;color:#213d38;line-height:1.65}table{width:100%;border-collapse:collapse;margin:35px 0}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}p{white-space:pre-wrap}@media print{body{margin:22mm}}</style></head><body>'+
 '<small>'+h(s().company?.legal_name||state.ws.name)+' · '+h(state.event?.name)+'</small><div>'+h(s().company?.billing_address||'').replace(/\n/g,'<br>')+'</div><div>'+h(s().company?.billing_email||'')+' '+h(s().company?.billing_phone||'')+'</div><div>Tax ID: '+h(s().company?.tax_number||'Not supplied')+'</div><h1>'+h(d.number)+' — '+h(d.title)+'</h1><p>'+h(d.body)+'</p><table><thead><tr><th>Service</th><th>Qty</th><th>Unit price</th><th>Amount</th></tr></thead><tbody>'+rows+'</tbody></table>'+
 '<p>Discount: '+money(d.discount_cents,d.subtotal_currency)+' · Tax: '+(d.tax_bps/100).toFixed(2)+'%</p><h2>Total: '+money(d.total_cents,d.subtotal_currency)+'</h2>'+
 (d.kind==='invoice'?'<p>Paid: '+money(d.paid_cents,d.subtotal_currency)+' · Balance: '+money(d.balance_cents,d.subtotal_currency)+'</p>':'')+
 '<p>'+h(d.terms)+'</p>'+(d.kind==='invoice'?'<h3>Payment instructions</h3><p>'+h(s().company?.payment_instructions||'Contact your planner for payment details')+'</p>':'')+'<p>Issued: '+h(d.issued_at||'Draft')+' · Due: '+h(d.due_date||'Not set')+'</p>'+
 (d.signer_name?'<p>Client acknowledged: '+h(d.signer_name)+' · '+h(d.responded_at)+'</p>':'')+
 '<script>window.onload=function(){window.print()}<\/script></body></html>';
 const url=URL.createObjectURL(new Blob([html],{type:'text/html'}));
 const tab=window.open(url,'_blank');
 if(!tab)toast('Enable popups to print or save a PDF');
 setTimeout(()=>URL.revokeObjectURL(url),60000);
}
async function act(action,id){
 const d=s().documents.find(x=>x.id===id);
 if(action==='company-edit')return editCompany();
 if(action==='portal-create')return editPortal(false);
 if(action==='portal-edit')return editPortal(true);
 if(action==='portal-open')return window.open(s().portal.url,'_blank','noopener,noreferrer');
 if(action==='portal-email')return callApi(()=>api(base()+'/portal/send',{method:'POST'}),'Portal link emailed. Share the PIN separately.');
 if(action==='copy'){try{await navigator.clipboard.writeText(location.origin+s().portal.url);toast('Copied')}catch{toast('Copy the portal URL shown above')}return}
 if(action.startsWith('new-'))return editDoc(action.slice(4));
 if(action==='view')return showDoc(id);
 if(action==='edit')return editDoc(d.kind,id);
 if(action==='payment')return paymentDialog(id);
 if(action==='notify')return callApi(()=>api(base()+'/documents/'+id+'/notify',{method:'POST'}),'Document notification sent');
 if(action==='issue'){if(confirm('Issue this document? It will become read-only.'))return callApi(()=>api(base()+'/documents/'+id+'/issue',{method:'POST'}),'Document issued');return}
 if(action==='invoice')return callApi(()=>api(base()+'/documents/'+id+'/duplicate',{method:'POST',body:{kind:'invoice'}}),'Invoice draft created');
 if(action==='void'){if(confirm('Void this invoice?'))return callApi(()=>api(base()+'/documents/'+id+'/void',{method:'POST'}),'Invoice voided')}
 if(action==='delete'){if(confirm('Delete this draft document permanently?'))return callApi(()=>api(base()+'/documents/'+id,{method:'DELETE'}),'Draft removed')}
}
document.addEventListener('click',e=>{
 const action=e.target.closest('[data-agency-action]');
 if(action&&state.page==='agencyDesk')act(action.dataset.agencyAction,action.dataset.id);
 const fi=e.target.closest('[data-agency-filter]');
 if(fi&&state.page==='agencyDesk'){filter=fi.dataset.agencyFilter;render()}
 if(e.target.closest('[data-agency-close]'))close();
 if(e.target.closest('[data-add-line]')){readLines();if(lines.length>=40){toast('Maximum 40 items');return}
   lines.push({description:'',quantity:1,unit_price_cents:0});document.getElementById('agencyLines').innerHTML=lineHtml()}
 const rem=e.target.closest('[data-remove-line]');
 if(rem){readLines();lines.splice(Number(rem.dataset.removeLine),1);document.getElementById('agencyLines').innerHTML=lineHtml()}
 const pr=e.target.closest('[data-agency-print]');if(pr)printDoc(pr.dataset.agencyPrint);
});
function renderPortfolio(){
 const results=state.agencyClients?.events||[];
 const active=results.filter(e=>e.client_name).length;
 const remaining=results.reduce((sum,e)=>sum+(e.outstanding_cents||0),0);
 return head('Agency clients & projects','One workspace-wide view of client portals, approvals, agreements and invoices.')+
 '<section class="agency-metrics"><div class="card"><small>All events</small><strong>'+results.length+'</strong></div>'+
 '<div class="card"><small>Client portals</small><strong>'+active+'</strong></div>'+
 '<div class="card"><small>Open proposals</small><strong>'+results.reduce((n,e)=>n+e.open_proposals,0)+'</strong></div>'+
 '<div class="card"><small>Outstanding invoices</small><strong>'+money(remaining)+'</strong></div>'+
 '<div class="card"><small>Invoiced revenue</small><strong>'+money(results.reduce((n,e)=>n+(e.invoiced_cents||0),0))+'</strong></div>'+
 '<div class="card"><small>Incurred project expenses</small><strong>'+money(results.reduce((n,e)=>n+(e.expense_cents||0),0))+'</strong></div>'+
 '<div class="card"><small>Invoiced margin</small><strong>'+money(results.reduce((n,e)=>n+(e.margin_cents||0),0))+'</strong></div></section>'+
 '<div class="agency-portfolio-export"><a class="btn" download href="/api'+prefix()+'/agency/advanced/report.csv">↓ Export agency financial report (CSV)</a></div>'+
 '<div class="agency-grid">'+(results.length?results.map(e=>'<article class="card agency-doc">'+
 '<div class="agency-doc-top"><small>'+h(e.type)+' · '+h(e.start_date)+'</small>'+status(e.status)+'</div>'+
 '<h3>'+h(e.name)+'</h3><p>Client: '+h(e.client_name||'Not invited')+'</p>'+
 '<p>'+e.document_count+' documents · '+e.open_proposals+' proposals pending · '+e.pending_contracts+' unsigned agreements</p>'+
 '<div class="agency-doc-amount"><strong>'+money(e.outstanding_cents)+'</strong><small>Outstanding from issued invoices</small></div>'+
 '<div class="agency-portfolio-project-metrics">Invoiced '+money(e.invoiced_cents||0)+' · Costs '+money(e.expense_cents||0)+' · Margin '+money(e.margin_cents||0)+'</div>'+
 '<div class="agency-buttons"><button type="button" class="btn primary" data-agency-event="'+h(e.id)+'">Open client desk ↗</button></div></article>').join(''):
 '<div class="card agency-empty"><h3>No events yet</h3><p>Create your first event to start a client portal.</p><button class="btn primary" onclick="openModal('+"'event'"+')">New event</button></div>')+'</div>';
}
document.addEventListener('click',e=>{
 const button=e.target.closest('[data-agency-event]');
 if(button&&state.page==='agencyClients')go('agencyDesk',button.dataset.agencyEvent);
});
window.EventOSAgency={render,renderPortfolio,printDoc};
})();