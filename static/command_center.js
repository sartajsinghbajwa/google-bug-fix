// Honestly Zen Event Planning Event Command Center — read-only overview of actual planning records.
'use strict';
function ccPercent(value){return Math.max(0,Math.min(100,Number(value)||0))}
function ccBar(value, tone='teal'){
  let p=ccPercent(value);
  return `<div class="cc-meter" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p}"><span class="cc-meter-fill cc-${tone}" style="width:${p}%"></span></div>`;
}
function ccLocalDaysUntil(iso, fallback){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(iso||'').slice(0,10)))return fallback;
  const [year,month,day]=iso.slice(0,10).split('-').map(Number);
  const date=new Date(year,month-1,day);
  if(date.getFullYear()!==year||date.getMonth()!==month-1||date.getDate()!==day)return fallback;
  const now=new Date();
  return Math.round((Date.UTC(year,month-1,day)-Date.UTC(now.getFullYear(),now.getMonth(),now.getDate()))/86400000);
}
function ccCountDown(days, status){
  if(status==='Completed')return {number:'✓',caption:'Event completed'};
  if(status==='Cancelled')return {number:'—',caption:'Event cancelled'};
  if(days===null||days===undefined)return {number:'—',caption:'Date not set'};
  if(days===0)return {number:'Today',caption:'It’s event day'};
  if(days>0)return {number:String(days),caption:days===1?'Day to go':'Days to go'};
  return {number:String(Math.abs(days)),caption:Math.abs(days)===1?'Day since event':'Days since event'};
}
function ccLink(label,page,eventId,ic='arrow'){
  return `<button type="button" class="cc-link" onclick="go('${page}','${eventId}')">${esc(label)}${icon(ic)}</button>`;
}
function ccKpi(label,value,hint,ic,tone=''){
  return `<div class="cc-kpi card"><div class="cc-kpi-header"><span>${esc(label)}</span><span class="cc-kpi-icon ${tone}">${icon(ic)}</span></div><div class="cc-kpi-value">${value}</div><div class="cc-kpi-caption">${esc(hint)}</div></div>`;
}
function ccAction(iconName,label,handler,primary=false){
  return `<button type="button" class="btn ${primary?'primary':''}" onclick="${handler}">${icon(iconName)} ${esc(label)}</button>`;
}
function renderEventCommandCenter(d, fallback){
  const e=d.event||fallback;
  if(!e)return '<div class="card pad">Loading this event…</div>';
  const meals=d.meals||{enabled:false,choices:[],selected:0};
  const g=d.guests||{},t=d.tasks||{},b=d.budget||{},v=d.vendors||{},schedule=d.schedule||[],alerts=d.attention||[];
  const countdown=ccCountDown(ccLocalDaysUntil(e.start_date,d.days_until),e.status);
  const complete=ccPercent(t.completion_rate),responded=ccPercent(g.response_rate);
  const percent=b.committed_percent===null||b.committed_percent===undefined?null:Number(b.committed_percent);
  const budgetRatio=percent===null?0:ccPercent(percent);
  const overdue=Number(t.overdue)||0, pending=Number(g.pending)||0;
  const eventId=e.id;
  const isViewer=state.ws?.role==='viewer';
  const canAdd=!isViewer;
  const alertText=alerts.length?`${alerts.length} planning item${alerts.length===1?'':'s'} to review`:'Nothing urgent on your planning list';
  const topActions=canAdd?`${ccAction('plus','Add task',"openModal('task')",true)}${ccAction('users','Add guest',"openModal('guest')")}${ccAction('wallet','Add expense',"openModal('budget')")}`:'';
  const nextTask=(t.next||[]).map(task=>{
    const isOverdue=task.due_date&&task.due_date<d.as_of_date;
    const when=task.due_date?fmtDate(task.due_date):'No due date';
    return `<div class="cc-line"><span class="cc-line-symbol ${isOverdue?'red':''}">${icon(isOverdue?'clock':'check')}</span><div class="cc-line-body"><strong>${esc(task.title)}</strong><small>${esc(when)} · ${esc(task.priority)} priority${task.assigned_to?' · '+esc(task.assigned_to):''}</small></div>${isOverdue?'<span class="cc-overdue">Overdue</span>':''}</div>`;
  }).join('');
  const milestones=schedule.map(s=>{
    let date=(s.start_at||'').slice(0,10),time=(s.start_at||'').slice(11,16);
    return `<div class="cc-line"><span class="cc-datebox"><strong>${esc(date.slice(8,10)||'—')}</strong><small>${esc(date?fmtDate(date).split(' ')[0]:'')}</small></span><div class="cc-line-body"><strong>${esc(s.title)}</strong><small>${esc(time||'Time TBD')}${s.location?' · '+esc(s.location):''}</small></div></div>`;
  }).join('');
  const alertList=alerts.map(a=>{
    const allowed=['tasks','budget','guests','eventVendors','schedule'];
    const page=allowed.includes(a.page)?a.page:'overview';
    return `<button type="button" class="cc-alert ${a.tone==='urgent'?'is-urgent':''}" onclick="go('${page}','${eventId}')"><span class="cc-alert-mark">${icon(a.tone==='urgent'?'clock':a.kind==='rsvp'?'mail':'spark')}</span><span class="cc-alert-text"><strong>${esc(a.title)}</strong><small>${esc(a.detail)}</small></span>${icon('arrow')}</button>`;
  }).join('');
  const titleBar=`<div class="cc-heading"><div><div class="cc-eyebrow">EVENT WORKSPACE / COMMAND CENTER</div><h1>${esc(e.name)}</h1><p>${esc(e.type)} · ${esc(fmtDate(e.start_date))} · ${esc(e.location||'Venue not set')}</p></div><div class="cc-heading-actions">${badge(e.status)}${canAdd?ccAction('file','Edit event',"openModal('editEvent')"):''}</div></div>`;
  const welcome=`<section class="cc-hero"><div class="cc-hero-copy"><div class="cc-eyebrow">PLAN · ORGANIZE · CELEBRATE</div><h2>${esc(e.name)}</h2><p>Keep your guests, plans, people, and spending moving together.</p><div class="cc-hero-tags"><span>${icon('calendar')} ${esc(fmtDate(e.start_date))}</span><span>${icon('location')} ${esc(e.location||'Venue TBD')}</span></div></div><div class="cc-countdown"><strong>${esc(countdown.number)}</strong><span>${esc(countdown.caption)}</span></div></section>`;
  return `<div class="cc-page">${titleBar}${welcome}
    <div class="cc-actions"><div><strong>Quick actions</strong><span>Jump back into planning</span></div><div class="cc-actions-buttons">${topActions}${ccAction('clock','Timeline',`go('schedule','${eventId}')`)}${ccAction('users','Event Operations',`go('operations','${eventId}')`)}</div></div>
    <section class="cc-kpis" aria-label="Event at a glance">
       ${ccKpi('Confirmed seats',esc(g.confirmed_seats||0),`${g.invitations||0} guest invitations`,'users')}
       ${ccKpi('RSVPs pending',esc(pending),`${g.response_rate||0}% of invitations responded`,'mail',pending?'amber':'')}
       ${ccKpi('Open tasks',esc(t.open||0),`${overdue} overdue · ${t.done||0} complete`,'check',overdue?'red':'')}
       ${ccKpi('Budget left',money(b.remaining_committable??e.budget), 'Target minus committed expenses','wallet',b.over_budget?'red':'')}
    </section>
    <div class="cc-main-grid">
      <section class="card cc-panel cc-priority"><div class="cc-panel-head"><div><span class="cc-panel-eyebrow">FOCUS FIRST</span><h2>Needs attention</h2><p>${esc(alertText)}</p></div><span class="cc-panel-bubble">${alerts.length}</span></div>${alertList||'<div class="cc-all-clear">'+icon('check')+'<span>All caught up on the tracked priorities. Your next steps will appear here as you plan.</span></div>'}</section>
      <section class="card cc-panel"><div class="cc-panel-head"><div><span class="cc-panel-eyebrow">CHECKLIST</span><h2>Planning progress</h2><p>${t.total?`${t.done||0} of ${t.total} tasks completed`:'Create tasks to start tracking progress'}</p></div><strong class="cc-big-percent">${t.total?complete+'%':'—'}</strong></div>${ccBar(complete)}<div class="cc-summary-stats"><span>${esc(t.open||0)} remaining</span><span>${esc(overdue)} overdue</span><span>${esc(t.due_soon||0)} due this week</span></div><div class="cc-section-subhead">Upcoming tasks</div>${nextTask||'<p class="cc-blank">No open tasks yet. Add a checklist item to begin.</p>'}${ccLink('Open checklist','tasks',eventId)}</section>
      <section class="card cc-panel"><div class="cc-panel-head"><div><span class="cc-panel-eyebrow">INVITATIONS</span><h2>Guest responses</h2><p>${g.invitations||0} invitations · ${g.invited_seats||0} seats invited</p></div><strong class="cc-big-percent">${g.invitations?responded+'%':'—'}</strong></div>${ccBar(responded)}<div class="cc-guest-breakdown"><span><i class="cc-dot green"></i>${esc(g.confirmed||0)} Confirmed</span><span><i class="cc-dot amber"></i>${esc(g.pending||0)} Pending</span><span><i class="cc-dot blue"></i>${esc(g.maybe||0)} Maybe</span><span><i class="cc-dot grey"></i>${esc(g.declined||0)} Declined</span></div><p class="cc-quiet">RSVP progress is based on invitation groups that have replied, not the number of seats.</p>${ccLink('Manage guests & RSVP','guests',eventId)}</section>
      ${meals.enabled?`<section class="card cc-panel"><div class="cc-panel-head"><div><span class="cc-panel-eyebrow">DINING</span><h2>Meal preferences</h2><p>${esc(meals.selected)} guest meal selection${meals.selected===1?'':'s'} received</p></div>${icon('check')}</div>
        <div class="cc-meal-choices">${meals.choices.map(choice=>`<div class="cc-meal-row"><span>${esc(choice.name)}</span><strong>${esc(choice.count)}</strong></div>`).join('')}</div>
        <p class="cc-quiet">Counts include only confirmed RSVPs with a selected meal. Unspecified preferences are not counted.</p>
        ${ccLink('View guest answers','guests',eventId)}</section>`:''}      <section class="card cc-panel"><div class="cc-panel-head"><div><span class="cc-panel-eyebrow">FINANCES</span><h2>Budget position</h2><p>${percent===null?'Set an event budget to track commitments':`${percent}% of target budget committed`}</p></div><strong class="cc-big-percent ${b.over_budget?'cc-negative':''}">${money(b.target??e.budget)}</strong></div>${ccBar(budgetRatio,b.over_budget?'danger':'teal')}<div class="cc-budget-grid"><div><span>Committed</span><strong>${money(b.committed)}</strong></div><div><span>Actual</span><strong>${money(b.actual)}</strong></div><div><span>Paid</span><strong>${money(b.paid)}</strong></div><div><span>Available</span><strong class="${b.over_budget?'cc-negative':''}">${money(b.remaining_committable??0)}</strong></div></div>${ccLink('Review expenses','budget',eventId)}</section>
      <section class="card cc-panel"><div class="cc-panel-head"><div><span class="cc-panel-eyebrow">RUN OF SHOW</span><h2>Upcoming milestones</h2><p>${d.schedule_count||0} total schedule items</p></div>${icon('clock')}</div>${milestones||'<p class="cc-blank">No upcoming timeline items. Add your next milestone or event-day activity.</p>'}${ccLink('Open event timeline','schedule',eventId)}</section>
      <section class="card cc-panel cc-vendor-panel"><div class="cc-panel-head"><div><span class="cc-panel-eyebrow">PEOPLE & PARTNERS</span><h2>Vendor coordination</h2><p>See who is booked and what is outstanding</p></div>${icon('store')}</div><div class="cc-vendor-counts"><div><strong>${esc(v.total||0)}</strong><span>Assigned</span></div><div><strong>${esc(v.booked||0)}</strong><span>Booked</span></div><div><strong>${esc(v.quotes_pending||0)}</strong><span>Quotes requested</span></div></div>${ccLink('Manage vendors','eventVendors',eventId)}<div class="cc-shortcuts"><span>Event essentials</span><div>${ccLink('Event details','details',eventId)}${ccLink('RSVP forms','guests',eventId)}</div></div></section>
    </div>
  </div>`;
}
