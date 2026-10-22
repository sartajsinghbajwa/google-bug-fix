/* Honestly Zen Event Planning customer account and support hub. Access checks are performed by the API. */
'use strict';
(function(){
const e=x=>window.esc(x);
const dt=x=>{if(!x)return '—';const v=new Date(x);return Number.isNaN(v.getTime())?'—':v.toLocaleDateString('en-CA',{year:'numeric',month:'short',day:'numeric'})};
const stat=(name,value,detail='')=>'<div class="as-stat"><span>'+e(name)+'</span><strong>'+e(value)+'</strong><small>'+e(detail)+'</small></div>';
const status=x=>'<span class="as-status status-'+e(String(x||'open').replaceAll('_','-'))+'">'+e(String(x||'open').replaceAll('_',' '))+'</span>';
const buttonLoading=(button,busy)=>{button.disabled=busy;button.dataset.text||=button.textContent;button.textContent=busy?'Please wait…':button.dataset.text};
const handle=async(form,work,message)=>{const button=form.querySelector('button[type=submit]');if(button)buttonLoading(button,true);try{await work();toast(message)}catch(err){toast(err.message)}finally{if(button)buttonLoading(button,false)}};
const empty=(text,action='')=>'<div class="as-empty"><strong>'+e(text)+'</strong><p>'+e(action)+'</p></div>';
const wsSummary=w=>'<article class="as-workspace"><div class="as-workspace-title"><div><span class="as-eyebrow">'+e(w.type)+' workspace</span><h3>'+e(w.name)+'</h3></div>'+status(w.billing_status||'not_configured')+'</div><div class="as-workspace-grid">'+stat('Plan',w.billing_plan||'Starter','Your subscription')+stat('Events',(w.events||0)+' / '+(w.max_events||0),'Usage')+stat('Guests',(w.guests||0)+' / '+(w.max_guests||0),'Usage')+stat('Team',(w.members||0)+' / '+(w.max_members||0),'Seats')+'</div><div class="as-actions"><button class="btn" type="button" onclick="EventOSAccount.goBilling('+JSON.stringify(w.id).replace(/"/g,'&quot;')+')">View plan & usage</button>'+(w.role==='owner'&&w.has_stripe_customer?'<button class="btn primary" type="button" onclick="EventOSAccount.openPortal('+JSON.stringify(w.id).replace(/"/g,'&quot;')+')">Manage payments &amp; invoices</button>':'')+'</div><small class="as-muted">'+(w.role==='owner'?'Workspace owner · Billing management available when a Stripe subscription is linked.':'Member · Ask the workspace owner to manage billing.')+'</small></article>';
function renderAccount(){
 const data=state.accountOverview||{},u=data.user||state.user||{},profile=data.profile||{},ws=data.workspaces||[];
 return head('My account','Manage your personal details, account security, subscriptions and support from one secure place.')+
 '<div class="as-hero"><div class="as-avatar">'+e(initials(u.full_name))+'</div><div><span class="as-eyebrow">MY EVENTOS PROFILE</span><h2>'+e(u.full_name||'Account')+'</h2><p>'+e(u.email||'')+' · Member since '+e(dt(u.created_at))+'</p></div><div class="as-hero-extra">'+status(u.email_verified?'Verified email':'Email verification pending')+'</div></div>'+
 '<div class="as-kpis">'+stat('Workspaces',ws.length,'Your memberships')+stat('Support tickets',Object.values(data.ticket_counts||{}).reduce((n,k)=>n+Number(k||0),0),'Submitted by you')+stat('Platform role',u.platform_role||'user','Account access')+'</div>'+
 '<div class="as-columns"><section class="card pad"><div class="as-section-head"><h2>Personal information</h2><p>Update your name, contact details and support preferences.</p></div><form onsubmit="EventOSAccount.saveProfile(event)" class="as-form">'+
 field('Full name','full_name',u.full_name||'','text','required maxlength="120"')+
 field('Phone (optional)','phone',profile.phone||'','tel','maxlength="40"')+
 field('Company / organization','company',profile.company||'','text','maxlength="140"')+
 field('Timezone (IANA)','timezone',profile.timezone||'UTC','text','required maxlength="80"')+
 '<label class="as-switch"><input type="checkbox" name="support_email_updates" '+(profile.support_email_updates?'checked':'')+'> Email me when support replies</label><button type="submit" class="btn primary">Save my details</button></form></section>'+
 '<section class="card pad"><div class="as-section-head"><h2>Login &amp; security</h2><p>Secure your Honestly Zen Event Planning account and sign-in information.</p></div><div class="as-security-card"><h3>Change password</h3><form class="as-form" onsubmit="EventOSAccount.changePassword(event)">'+field('Current password','current_password','','password','required autocomplete="current-password"')+field('New password (12+ characters)','new_password','','password','required minlength="12" maxlength="256" autocomplete="new-password"')+field('Confirm new password','confirm_password','','password','required minlength="12" maxlength="256" autocomplete="new-password"')+'<button class="btn primary" type="submit">Update password</button></form><small class="as-muted">Changing your password signs out your other sessions.</small></div><div class="as-security-card"><h3>Change email</h3><p class="small-note">For security, we verify the new address before changing your login email.</p><form class="as-form" onsubmit="EventOSAccount.changeEmail(event)">'+field('New email address','new_email','','email','required maxlength="255" autocomplete="email"')+field('Current password','current_password','','password','required autocomplete="current-password"')+'<button class="btn" type="submit">Send confirmation link</button></form><small class="as-muted">Requires email delivery (SMTP) to be configured. The confirmation link expires after 1 hour.</small></div></section></div>'+
 renderMailPreferences()+
 '<section class="card pad m-top"><div class="as-section-head"><h2>My billing &amp; subscriptions</h2><p>Plans, usage, access and payment management for your workspaces.</p></div><div class="as-workspaces">'+(ws.length?ws.map(wsSummary).join(''):empty('No workspaces found','Please contact support if you cannot access your workspace.'))+'</div></section>'+
 '<section class="as-help-strip"><div><strong>Need a hand with Honestly Zen Event Planning?</strong><p>Search the Help Center or open a private ticket.</p></div><button class="btn" type="button" onclick="go(\'help\')">Browse Help Center</button><button class="btn primary" type="button" onclick="go(\'support\')">Visit Support Center →</button></section>';
}
function field(title,name,value,type='text',extra=''){
 return '<label class="field"><span>'+e(title)+'</span><input name="'+name+'" type="'+type+'" value="'+e(value)+'" '+extra+'></label>';
}
function ticketItem(t,admin){
 return '<button type="button" class="as-ticket-item" onclick="go(\''+(admin?'supportInbox':'support')+'\',\''+e(t.id)+'\')"><div><span class="as-eyebrow">'+e(t.ticket_number)+' · '+e(t.category)+'</span><strong>'+e(t.subject)+'</strong><small>'+e(admin?(t.customer_name+' · '+t.customer_email):('Updated '+dt(t.updated_at)))+'</small></div><div class="as-ticket-right">'+status(t.status)+'<small>'+e(t.priority)+' priority</small></div></button>';
}
function renderMailPreferences(){
 const data=state.accountEmailPrefs||{},prefs=data.preferences||{},groups=[
 ['events','Event updates','New events and important status changes'],
 ['rsvp','RSVP responses','Guest responses and attendance updates'],
 ['vendors','Vendor quotes & negotiations','New quotes and vendor replies'],
 ['contracts','Contracts','Agreement acknowledgements and updates'],
 ['billing','Billing & invoices','Subscription changes and confirmed invoice payments'],
 ['support','Support messages','Ticket replies and status changes'],
 ['announcements','News & announcements','Optional community and platform announcements']];
 return '<section class="card pad m-top"><div class="as-section-head"><h2>Email notification preferences</h2><p>Choose which Honestly Zen Event Planning updates you receive by email. Account security messages cannot be disabled. SMTP delivery is '+(data.delivery_configured?'enabled':'not configured yet')+'.</p></div><form class="as-form" onsubmit="EventOSAccount.saveEmailPrefs(event)">'+groups.map(([key,label,detail])=>'<label class="as-switch"><input type="checkbox" name="'+key+'" '+(prefs[key]?'checked':'')+'><span><strong>'+e(label)+'</strong><small style="display:block;color:var(--muted)">'+e(detail)+'</small></span></label>').join('')+'<div class="as-muted">Security alerts, verification links and password resets are always transactional.</div><button type="submit" class="btn primary">Save email preferences</button></form></section>';
}
function renderSupport(admin=false){
 const data=admin?state.supportAdmin:state.supportData;
 const tickets=data?.tickets||[],ticket=state.supportTicket||null,draft=!admin?(window.EventOSHelp?.draft||null):null;
 if(state.eventId&&ticket?.ticket?.id===state.eventId)return renderTicketDetail(ticket,admin);
 const outstanding=tickets.filter(x=>!['closed','resolved'].includes(x.status)).length;
 return head(admin?'Support administration':'Support Center',admin?'Manage incoming support requests and reply securely.':'Create a ticket, track progress and message the Honestly Zen Event Planning support team.')+
 '<div class="as-kpis">'+stat('Total tickets',tickets.length,'All visible requests')+stat('Open / in progress',outstanding,'Awaiting resolution')+stat('Resolved',tickets.filter(x=>['closed','resolved'].includes(x.status)).length,'Completed requests')+'</div>'+
 (admin?'<div class="as-help-strip"><div><strong>Administrator support queue</strong><p>Only platform admins can view all customer tickets or change status.</p></div><button class="btn" onclick="EventOSAccount.reloadSupport()">Refresh queue</button></div>':
 '<div class="as-help-strip"><div><strong>Find a quick answer</strong><p>Browse Personal and Business setup guides, FAQs and troubleshooting.</p></div><button class="btn" type="button" onclick="go(\'help\')">Browse Help Center →</button></div><section class="card pad m-top"><div class="as-section-head"><h2>Submit a support request</h2><p>Describe what you need help with. Do not include passwords or payment-card details.</p></div><form class="as-form as-ticket-create" onsubmit="EventOSAccount.createTicket(event)">'+field('Subject','subject',draft?.subject||'','text','required minlength="5" maxlength="160"')+'<div class="as-two">'+
 '<label class="field"><span>Category</span><select name="category" required><option value="technical"'+(draft?.category==='technical'?' selected':'')+'>Technical issue</option><option value="billing"'+(draft?.category==='billing'?' selected':'')+'>Billing / subscription</option><option value="account"'+(draft?.category==='account'?' selected':'')+'>Account & login</option><option value="event"'+(draft?.category==='event'?' selected':'')+'>Event management</option><option value="other"'+(draft?.category==='other'?' selected':'')+'>Other</option></select></label>'+
 '<label class="field"><span>Priority</span><select name="priority"><option value="normal">Normal</option><option value="low">Low</option><option value="high">High</option></select></label></div>'+workspacePicker()+'<label class="field"><span>What happened? (15–5000 characters)</span><textarea name="message" rows="5" minlength="15" maxlength="5000" required placeholder="Include the page, the steps you tried, and any error message.">'+e(draft?.message||'')+'</textarea></label><button class="btn primary" type="submit">Create support ticket</button></form></section>')+
 '<section class="card pad m-top"><div class="as-section-head"><h2>'+(admin?'All customer tickets':'My tickets')+'</h2><p>'+(admin?'Latest incoming requests across the platform.':'Your requests and replies stay in your private account.')+'</p></div><div class="as-ticket-list">'+(tickets.length?tickets.map(t=>ticketItem(t,admin)).join(''):empty('No tickets yet','Create your first request above whenever you need assistance.'))+'</div></section>';
}
function workspacePicker(){
 const workspaces=state.accountOverview?.workspaces||state.workspaces||[];
 return '<label class="field"><span>Related workspace (optional)</span><select name="workspace_id"><option value="">General account question</option>'+workspaces.map(x=>'<option value="'+e(x.id)+'">'+e(x.name)+'</option>').join('')+'</select></label>';
}
function renderTicketDetail(data,admin){
 const t=data.ticket,msg=data.messages||[],states=['open','in_progress','waiting_on_customer','resolved','closed'];
 return head('Ticket '+t.ticket_number,'Private support conversation')+
 '<div class="as-ticket-header card pad"><div><button class="btn tiny" onclick="go(\''+(admin?'supportInbox':'support')+'\')">← All tickets</button><span class="as-eyebrow">'+e(t.category)+' · '+e(t.priority)+' priority</span><h2>'+e(t.subject)+'</h2><p>Opened '+e(dt(t.created_at))+' · Last activity '+e(dt(t.updated_at))+'</p>'+(admin?'<p>Customer: '+e(t.customer_name)+' ('+e(t.customer_email)+')'+(t.workspace_name?' · '+e(t.workspace_name):'')+'</p>':'')+'</div>'+status(t.status)+'</div>'+
 (admin?'<div class="card pad m-top"><label class="field"><span>Ticket status</span><select id="supportStatus" onchange="EventOSAccount.updateStatus(\''+e(t.id)+'\',this.value)">'+states.map(x=>'<option value="'+x+'" '+(x===t.status?'selected':'')+'>'+x.replaceAll('_',' ')+'</option>').join('')+'</select></label></div>':'')+
 '<section class="card pad m-top"><h2>Conversation</h2><div class="as-thread">'+msg.map(m=>'<article class="as-message '+(m.author_role==='staff'?'staff':'')+'"><div><strong>'+e(m.author_role==='staff'?'Honestly Zen Event Planning support':m.author_name)+'</strong><small>'+e(dt(m.created_at))+'</small></div><p>'+e(m.body)+'</p></article>').join('')+'</div></section>'+
 (t.status!=='closed'?'<section class="card pad m-top"><h2>Reply</h2><form class="as-form" onsubmit="EventOSAccount.reply(event,\''+e(t.id)+'\')"><label class="field"><span>Message</span><textarea name="message" rows="5" minlength="2" maxlength="5000" required placeholder="Write a helpful reply..."></textarea></label><button class="btn primary" type="submit">Send reply</button></form></section>':'<p class="as-muted m-top">This ticket is closed. Start a new ticket to request further help.</p>');
}
async function reloadSupport(){
 if(state.page==='supportInbox')state.supportAdmin=await api('/platform/support');
 else state.supportData=await api('/support/tickets');
 if(state.eventId)state.supportTicket=await api('/support/tickets/'+encodeURIComponent(state.eventId));
 render();
}
async function afterProfile(){const data=await api('/account/overview');state.accountOverview=data;state.user={...state.user,...data.user};render()}
window.EventOSAccount={
 renderAccount,renderSupport,
 saveEmailPrefs:async event=>{event.preventDefault();const form=event.target;await handle(form,async()=>{const data={};for(const key of ['events','rsvp','vendors','contracts','billing','support','announcements'])data[key]=form.elements[key].checked;await api('/account/email-preferences',{method:'PATCH',body:data});state.accountEmailPrefs=await api('/account/email-preferences');render()},'Email preferences saved')},
 reloadSupport:async()=>{try{await reloadSupport()}catch(err){toast(err.message)}},
 saveProfile:async event=>{event.preventDefault();const form=event.target;await handle(form,async()=>{const d=Object.fromEntries(new FormData(form));d.support_email_updates=form.elements.support_email_updates.checked;await api('/account/profile',{method:'PATCH',body:d});await afterProfile()},'Your profile was updated')},
 changePassword:async event=>{event.preventDefault();const form=event.target;const d=Object.fromEntries(new FormData(form));if(d.new_password!==d.confirm_password){toast('New passwords do not match');return}await handle(form,async()=>{await api('/account/password',{method:'POST',body:{current_password:d.current_password,new_password:d.new_password}});form.reset()},'Password changed; other sessions signed out')},
 changeEmail:async event=>{event.preventDefault();const form=event.target;await handle(form,async()=>{await api('/account/email-change',{method:'POST',body:Object.fromEntries(new FormData(form))});form.reset()},'Confirmation sent to your new email address')},
 createTicket:async event=>{event.preventDefault();const form=event.target;await handle(form,async()=>{const ticket=await api('/support/tickets',{method:'POST',body:Object.fromEntries(new FormData(form))});state.supportData=await api('/support/tickets');if(window.EventOSHelp)window.EventOSHelp.draft=null;go('support',ticket.id)},'Ticket created successfully')},
 reply:async(event,id)=>{event.preventDefault();const form=event.target;await handle(form,async()=>{await api('/support/tickets/'+encodeURIComponent(id)+'/reply',{method:'POST',body:Object.fromEntries(new FormData(form))});state.supportTicket=await api('/support/tickets/'+encodeURIComponent(id));render()},'Reply sent')},
 updateStatus:async(id,s)=>{try{await api('/platform/support/'+encodeURIComponent(id),{method:'PATCH',body:{status:s}});state.supportTicket=await api('/support/tickets/'+encodeURIComponent(id));render();toast('Ticket status updated')}catch(err){toast(err.message);render()}},
 goBilling:wid=>{const workspace=state.workspaces.find(x=>x.id===wid);if(!workspace){toast('Workspace is not accessible');return}state.ws=workspace;go('billing')},
 openPortal:async wid=>{try{const d=await api('/w/'+encodeURIComponent(wid)+'/billing/portal',{method:'POST'});if(!d.portal_url?.startsWith('https://'))throw Error('Invalid payment provider URL');location.assign(d.portal_url)}catch(err){toast(err.message)}},
 renderEmailConfirmation:()=>{
    if(!location.hash.startsWith('#/confirm-email-change?'))return false;
    const token=new URLSearchParams(location.hash.split('?')[1]||'').get('token')||'';
    document.getElementById('app').innerHTML='<div class="auth-page"><main class="auth-panel"><div class="auth-box"><div class="small-label">EVENTOS ACCOUNT SECURITY</div><h2>Confirm your new email</h2><p>The link works once and expires after 1 hour.</p><button type="button" class="btn primary full" id="asEmailConfirm">Confirm email address</button><p id="asEmailResult" role="status"></p><a href="/app">Return to sign in</a></div></main></div>';
    document.getElementById('asEmailConfirm').onclick=async()=>{const button=document.getElementById('asEmailConfirm');button.disabled=true;try{await api('/account/email-confirm',{method:'POST',body:{token}});state.user=null;document.getElementById('asEmailResult').textContent='Email updated. Sign in again with your new address.';button.hidden=true}catch(err){document.getElementById('asEmailResult').textContent=err.message;button.disabled=false}};
    return true;
 }
};
})();
