/* Phase 12 agency delivery studio; mounted into the existing Agency Client Desk. */
'use strict';
(()=>{
 const detail=()=>state.agencyAdvanced||null;
 const path=()=>evpath()+'/agency/advanced';
 const h=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const price=(x,c)=>new Intl.NumberFormat('en-CA',{style:'currency',currency:c||'CAD'}).format((Number(x)||0)/100);
 const ccy=()=>detail()?.currency||'CAD';
 const btn=(label,action,id='',extra='')=>'<button class="btn tiny" type="button" data-advanced-action="'+action+'" data-id="'+h(id)+'" '+extra+'>'+h(label)+'</button>';
 const stageName=s=>({planned:'Planned',in_progress:'In progress',completed:'Completed',blocked:'Blocked',shortlisted:'Shortlisted',booked:'Booked',complete:'Complete',cancelled:'Cancelled',incurred:'Incurred',paid:'Paid'})[s]||s;
 const options=(values,current)=>values.map(k=>'<option value="'+h(k)+'" '+(k===current?'selected':'')+'>'+h(stageName(k))+'</option>').join('');
 const warn=(text)=>'<p class="small-note">'+h(text)+'</p>';
 const categories=['Venue','Catering','Decor','Florals','Photography','Entertainment','Transport','Staff','Technology','Other'];
 function render(){
  const d=detail();
  if(!d)return '<section class="card pad m-top"><h2>Agency project delivery</h2><p class="small-note">Loading agency financial and project data…</p></section>';
  const m=d.metrics,edit=d.can_edit;
  const metrics=[
   ['Invoiced (issued)',m.invoiced_cents],['Payments recorded',m.received_cents],
   ['Outstanding invoices',m.receivable_cents],['Expenses incurred',m.incurred_expenses_cents],
   ['Invoiced margin',m.invoiced_margin_cents],['Cash position',m.cash_position_cents]
  ];
  return '<section class="agency-advanced">'+
   '<div class="agency-advanced-heading"><div><small>PHASE 12 · AGENCY OPERATIONS</small><h2>Project delivery & profitability</h2>'+
   warn('Track client milestones, vendor assignments, expenses and margins in one place. Calculations use issued invoices and recorded expenses, not potential sales or unpaid deposits.')+
   '</div><a class="btn" href="/api'+path()+'/report.csv" download>↓ Financial report CSV</a></div>'+
   '<div class="agency-advanced-metrics">'+metrics.map(([label,cents])=>'<div class="card"><small>'+h(label)+'</small><strong>'+price(cents,ccy())+'</strong></div>').join('')+'</div>'+
   '<p class="small-note">Invoiced margin: <strong>'+(m.invoiced_margin_pct===null?'Not available':h(m.invoiced_margin_pct)+'%')+
   '</strong> · Vendor commitments: '+price(m.vendor_committed_cents,ccy())+
   ' · Planned expenses: '+price(m.planned_expenses_cents,ccy())+
   '. Vendor commitments and expense entries are separate records—do not enter the same cost twice as an incurred expense.</p>'+
   '<div class="agency-advanced-columns"><section class="card pad">'+
    '<h3>Client project milestones</h3>'+warn('A client sees only milestones specifically marked visible in their PIN-protected portal.')+
    (edit?'<form class="agency-advanced-form" data-advanced-form="milestones">'+
      '<label>Milestone<input required name="title" maxlength="150" placeholder="Final venue walkthrough"></label>'+
      '<label>Due date<input name="due_date" type="date"></label>'+
      '<label>Status<select name="status">'+options(['planned','in_progress','completed','blocked'],'planned')+'</select></label>'+
      '<label class="full">Details<textarea name="details" maxlength="1000" rows="2" placeholder="Deliverable or next step"></textarea></label>'+
      '<label class="check"><input name="client_visible" type="checkbox"> Share progress with client</label>'+
      '<button class="btn primary" type="submit">Add milestone</button></form>':'')+
    '<div class="agency-advanced-list">'+(d.milestones.length?d.milestones.map(x=>
      '<article class="agency-advanced-line"><div><strong>'+h(x.title)+'</strong><small>'+h(x.due_date||'No due date')+' · '+(x.client_visible?'Client can see':'Internal only')+'</small>'+
      (x.details?'<p>'+h(x.details)+'</p>':'')+'</div>'+(edit?'<div class="agency-advanced-actions">'+
      '<select aria-label="Milestone status" data-milestone-select="'+h(x.id)+'">'+options(['planned','in_progress','completed','blocked'],x.status)+'</select>'+
      btn('Save status','milestone-status',x.id)+btn(x.client_visible?'Hide from client':'Share with client','milestone-visible',x.id)+
      btn('Delete','milestone-delete',x.id)+'</div>':'<span>'+h(stageName(x.status))+'</span>')+
      '</article>').join(''):warn('No project milestones yet.'))+'</div></section>'+
   '<section class="card pad"><h3>Vendor assignments</h3>'+
   warn('Assign companies from your existing workspace vendor directory and track scope and committed amounts. These are commitments, not automatically booked or paid transactions.')+
   (edit?'<form class="agency-advanced-form" data-advanced-form="vendors">'+
      '<label>Choose vendor<select name="vendor_id" required><option value="">Select a vendor</option>'+
      d.workspace_vendors.map(v=>'<option value="'+h(v.id)+'">'+h(v.name)+' · '+h(v.category)+'</option>').join('')+'</select></label>'+
      '<label>Commitment ('+h(ccy())+')<input name="committed" type="number" min="0" max="10000000" step="0.01" value="0" required></label>'+
      '<label>Status<select name="status">'+options(['shortlisted','booked','in_progress','complete','cancelled'],'shortlisted')+'</select></label>'+
      '<label>Due date<input name="due_date" type="date"></label>'+
      '<label class="full">Scope<textarea name="scope" maxlength="650" rows="2" placeholder="Catering for 150 guests"></textarea></label>'+
      '<button class="btn primary" type="submit">Save vendor assignment</button></form>':'')+
   (!d.workspace_vendors.length?warn('Add vendors under Vendor Directory before creating assignments.'):'')+
   '<div class="agency-advanced-list">'+(d.vendor_assignments.length?d.vendor_assignments.map(x=>
     '<article class="agency-advanced-line"><div><strong>'+h(x.vendor_name)+'</strong><small>'+h(stageName(x.status))+' · '+h(x.due_date||'No due date')+'</small>'+
     '<p>'+h(x.scope||'Scope not set')+'</p><b>'+price(x.committed_cents,ccy())+'</b></div>'+
     (edit?'<div>'+btn('Remove','vendor-delete',x.id)+'</div>':'')+'</article>').join(''):warn('No vendors assigned to this project.'))+'</div></section></div>'+
   '<section class="card pad m-top"><h3>Agency expenses & financial records</h3>'+
    warn('Record planning costs as Planned, Incurred, or Paid. Only incurred/paid expenses affect margin; only Paid expenses reduce the cash position.')+
   (edit?'<form class="agency-advanced-form expense-form" data-advanced-form="expenses">'+
      '<label>Expense<input name="title" maxlength="180" required placeholder="Decor and installation"></label>'+
      '<label>Category<select name="category">'+categories.map(c=>'<option value="'+c+'">'+c+'</option>').join('')+'</select></label>'+
      '<label>Amount ('+h(ccy())+')<input name="amount" type="number" min="0.01" max="10000000" step="0.01" required></label>'+
      '<label>Vendor (optional)<select name="vendor_id"><option value="">None</option>'+
      d.workspace_vendors.map(v=>'<option value="'+h(v.id)+'">'+h(v.name)+'</option>').join('')+'</select></label>'+
      '<label>Status<select name="status">'+options(['planned','incurred','paid'],'planned')+'</select></label>'+
      '<label>Date<input name="incurred_on" type="date"></label>'+
      '<label class="full">Private note<input name="note" maxlength="800" placeholder="Internal expense memo; not shown to client"></label>'+
      '<button class="btn primary" type="submit">Record expense</button></form>':'')+
   '<div class="agency-advanced-list">'+(d.expenses.length?d.expenses.map(x=>
     '<article class="agency-advanced-line"><div><strong>'+h(x.title)+'</strong><small>'+h(x.category)+
       ' · '+h(x.vendor_name||'No vendor')+' · '+h(x.incurred_on||'Undated')+'</small>'+
       '<b>'+price(x.amount_cents,ccy())+'</b>'+(x.note?'<p>'+h(x.note)+'</p>':'')+'</div>'+
       (edit?'<div class="agency-advanced-actions"><select data-expense-select="'+h(x.id)+'" aria-label="Expense status">'+options(['planned','incurred','paid'],x.status)+'</select>'+
       btn('Save status','expense-status',x.id)+btn('Delete','expense-delete',x.id)+'</div>':'<span>'+h(stageName(x.status))+'</span>')+
     '</article>').join(''):warn('No expense entries recorded.'))+'</div>'+
    (d.payments_needing_review.length?'<p class="agency-advanced-review">Stripe test checkouts requiring manual review: '+d.payments_needing_review.length+
      '. Do not credit payments until independently reconciled.</p>':'')+'</section>'+
   '<section class="card pad m-top"><h3>Invoices & signature evidence</h3><p class="small-note">Client contract acknowledgments include a downloadable evidence receipt, not a certified e-signature. Test payments are available only after an approved test-workspace Stripe configuration; live charges are not enabled.</p>'+
    '<p class="small-note">Stripe agency test checkout: <strong>'+(d.stripe_test_enabled?'Enabled for this test workspace':'Not configured')+'</strong></p>'+
    '</section></section>';
 }
 async function refresh(message){
  try{state.agencyAdvanced=await api(path());window.render();if(message)toast(message)}
  catch(e){toast(e.message)}
 }
 async function perform(fn,message){
  try{await fn();await refresh(message)}
  catch(e){toast(e.message)}
 }
 function cents(text){const amount=Number(text);if(!Number.isFinite(amount)||amount<0)return NaN;return Math.round(amount*100)}
 document.addEventListener('submit',e=>{
  const form=e.target.closest('[data-advanced-form]');
  if(!form||state.page!=='agencyDesk')return;
  e.preventDefault();if(!detail()?.can_edit)return;
  const body=Object.fromEntries(new FormData(form));
  const kind=form.dataset.advancedForm;
  if(kind==='milestones'){
   body.client_visible=Boolean(form.querySelector('[name="client_visible"]')?.checked);
   perform(()=>api(path()+'/milestones',{method:'POST',body}),'Milestone saved');
  }
  if(kind==='vendors'){
   const sum=cents(body.committed);if(!Number.isInteger(sum)){toast('Enter a valid vendor amount');return}
   perform(()=>api(path()+'/vendors',{method:'POST',body:{
    vendor_id:body.vendor_id,scope:body.scope,status:body.status,due_date:body.due_date,committed_cents:sum
   }}),'Vendor assignment saved');
  }
  if(kind==='expenses'){
   const sum=cents(body.amount);if(!Number.isInteger(sum)||sum<1){toast('Enter a positive expense');return}
   perform(()=>api(path()+'/expenses',{method:'POST',body:{
     title:body.title,category:body.category,amount_cents:sum,vendor_id:body.vendor_id||null,
     incurred_on:body.incurred_on,status:body.status,note:body.note
   }}),'Expense saved');
  }
 });
 document.addEventListener('click',e=>{
  const a=e.target.closest('[data-advanced-action]');
  if(!a||state.page!=='agencyDesk')return;
  const action=a.dataset.advancedAction,id=a.dataset.id;
  if(!detail()?.can_edit)return;
  if(action==='milestone-status'){
   const status=document.querySelector('[data-milestone-select="'+id+'"]')?.value;
   perform(()=>api(path()+'/milestones/'+encodeURIComponent(id),{method:'PATCH',body:{status}}),'Milestone updated');
  }
  if(action==='milestone-visible'){
   const row=detail().milestones.find(v=>v.id===id);if(!row)return;
   perform(()=>api(path()+'/milestones/'+encodeURIComponent(id),{method:'PATCH',
     body:{client_visible:!row.client_visible}}),'Client milestone visibility updated');
  }
  if(action==='expense-status'){
   const status=document.querySelector('[data-expense-select="'+id+'"]')?.value;
   perform(()=>api(path()+'/expenses/'+encodeURIComponent(id),{method:'PATCH',body:{status}}),'Expense status updated');
  }
  const deletions={'milestone-delete':'milestones','vendor-delete':'vendors','expense-delete':'expenses'};
  if(deletions[action]&&confirm('Permanently delete this '+deletions[action].slice(0,-1)+' record?')){
   perform(()=>api(path()+'/'+deletions[action]+'/'+encodeURIComponent(id),{method:'DELETE'}),'Record deleted');
  }
 });
 window.EventOSAgencyAdvanced={render,refresh};
})();