"""DIY celebration planner, transparent budget allocations and quote comparisons.

This module adds only two tables; it uses existing EventOS events, tasks, budgets,
marketplace quotes and CRM vendors. All reads and writes enforce workspace access.
Actual payments are never inferred from quote amounts or marketplace offers.
"""
from __future__ import annotations
import re
import uuid
from datetime import date, timedelta
from fastapi import HTTPException, Request

CATEGORIES = ('Venue & rentals','Food & drinks','Cake & desserts','Decor & flowers',
              'Photography & video','Entertainment','Attire & beauty','Invitations & stationery',
              'Transport','Staffing & services','Gifts & favours','Other & contingency')
# Suggested planning percentages, not vendor price forecasts.
BUDGET_WEIGHTS = {
  'Wedding': (27,25,6,10,10,7,5,3,2,2,1,2),
  'Birthday': (17,28,12,12,5,14,0,3,1,2,2,4),
  'Kids Party': (15,25,13,15,5,15,0,3,1,2,2,4),
  'Baby Shower': (17,28,12,18,5,9,0,3,1,2,2,3),
  'Other Event': (24,25,8,12,8,10,2,3,1,2,1,4),
}
TEMPLATES = {
  'Wedding': ('Decide ceremony and reception priorities','Draft your guest list','Shortlist ceremony and reception venues','Request catering quotes','Book photographer or videographer','Plan ceremony, seating and RSVP','Confirm vendors, payments and final timeline'),
  'Birthday': ('Set a guest count and party budget','Choose party theme and date','Find venue or hosting space','Send invitations and track RSVPs','Plan food, cake and drinks','Choose games or entertainment','Confirm setup and event-day checklist'),
  'Anniversary': ('Agree on a celebration style and budget','Prepare guest list or intimate itinerary','Find venue or restaurant','Plan menu and cake','Choose music and personal touches','Send invitations and track RSVPs','Confirm vendors and final schedule'),
  'Baby Shower': ('Choose host, date and budget','Pick theme and venue','Set guest list','Send invitations and collect RSVPs','Plan snacks, cake and games','Organize gifts and decorations','Confirm setup and cleanup'),
  'Kids Party': ('Choose age-appropriate theme and schedule','Set budget and guest count','Choose location and safety plan','Arrange food, cake and allergies','Plan games and entertainment','Send invitations and confirm guardians','Prepare day-of checklist'),
  'Other Event': ('Set your celebration goals','Set budget and guest count','Find and compare venue options','Plan food and drinks','Send invitations and track RSVPs','Request and compare vendor quotes','Confirm timeline and payment plan'),
}
DDL = {
 'diy_event_plans': '''CREATE TABLE IF NOT EXISTS diy_event_plans (
   event_id VARCHAR(191) PRIMARY KEY, workspace_id VARCHAR(191) NOT NULL,
   planning_style VARCHAR(40) NOT NULL DEFAULT 'balanced',
   theme VARCHAR(160) NOT NULL DEFAULT '', notes TEXT NOT NULL,
   updated_at VARCHAR(40) NOT NULL DEFAULT '')''',
 'diy_budget_allocations': '''CREATE TABLE IF NOT EXISTS diy_budget_allocations (
   event_id VARCHAR(191) NOT NULL, workspace_id VARCHAR(191) NOT NULL,
   category VARCHAR(80) NOT NULL, target DECIMAL(12,2) NOT NULL DEFAULT 0,
   updated_at VARCHAR(40) NOT NULL DEFAULT '', PRIMARY KEY(event_id,category))''',
}

def migrate(db):
 with db() as con:
  for statement in DDL.values():con.execute(statement)

def valid_amount(value):
 if type(value) not in (float,int) or not 0 <= value <= 10000000:raise HTTPException(422,'Use an amount between 0 and 10,000,000')
 return round(value,2)

def install(app,db,access,check_event,mariadb=False):
 if not mariadb:migrate(db)
 from datetime import datetime,timezone
 now=lambda:datetime.now(timezone.utc).isoformat(timespec='seconds')

 @app.get('/api/w/{wid}/diy/meta')
 def meta(request:Request,wid:str):
  with db() as con:access(request,con,wid)
  return {'categories':CATEGORIES,'templates':list(TEMPLATES),'note':'Prices and estimates are user entered; vendor offers do not automatically count as paid expenses.'}

 @app.get('/api/w/{wid}/diy/events/{eid}')
 def event_plan(request:Request,wid:str,eid:str):
  with db() as con:
   access(request,con,wid)
   ev=dict(check_event(con,wid,eid))
   ws=con.execute('SELECT currency FROM workspaces WHERE id=?',(wid,)).fetchone()
   plan=con.execute('SELECT planning_style,theme,notes,updated_at FROM diy_event_plans WHERE workspace_id=? AND event_id=?',(wid,eid)).fetchone()
   alloc=[dict(r) for r in con.execute('SELECT category,target FROM diy_budget_allocations WHERE workspace_id=? AND event_id=? ORDER BY category',(wid,eid))]
   tasks=[dict(r) for r in con.execute('SELECT id,title,status,due_date,priority FROM tasks WHERE workspace_id=? AND event_id=? ORDER BY due_date,title',(wid,eid))]
   costs=[dict(r) for r in con.execute('SELECT id,name,category,estimated,committed,actual,paid FROM budget_items WHERE workspace_id=? AND event_id=? ORDER BY category,name',(wid,eid))]
   vendors=[dict(r) for r in con.execute('''SELECT v.name,v.category,ev.status,ev.committed,ev.vendor_id
     FROM event_vendors ev JOIN vendors v ON v.id=ev.vendor_id
     WHERE ev.workspace_id=? AND ev.event_id=? ORDER BY v.name''',(wid,eid))]
   # Host may read only quotes belonging to this exact workspace/event.
   quotes=[dict(r) for r in con.execute('''SELECT q.id,q.listing_id,q.status,q.offer_amount,q.budget,q.event_date,
      l.business_name,l.category,l.city,l.price_mode,l.starting_price,l.verification_status,
      (SELECT COUNT(*) FROM market_reviews r WHERE r.listing_id=l.id AND r.status='published') review_count,
      (SELECT AVG(r.rating) FROM market_reviews r WHERE r.listing_id=l.id AND r.status='published') avg_rating
      FROM market_quotes q JOIN market_listings l ON l.id=q.listing_id
      WHERE q.workspace_id=? AND q.event_id=? ORDER BY l.category,l.business_name''',(wid,eid))]
  sums={field:round(sum(float(row[field] or 0) for row in costs),2) for field in ('estimated','committed','actual','paid')}
  goal=round(float(ev['budget'] or 0),2)
  assigned=round(sum(float(row['target'] or 0) for row in alloc),2)
  return {'event':ev,'plan':dict(plan) if plan else None,'categories':CATEGORIES,
          'template':TEMPLATES.get(ev['type'],TEMPLATES['Other Event']),'allocations':alloc,
          'allocation_total':assigned,'unallocated':round(goal-assigned,2),
          'tasks':tasks,'costs':costs,'totals':{**sums,'goal':goal,'remaining_vs_committed':round(goal-sums['committed'],2),
               'remaining_vs_actual':round(goal-sums['actual'],2),'unpaid_actual':round(max(0,sums['actual']-sums['paid']),2)},
          'vendors':vendors,'quotes':quotes,'currency':ws['currency'] if ws else 'CAD',
          'caveat':'Quotes and estimates are not expenses. Paid is recorded by you; it is not verified bank or Stripe settlement.'}

 @app.put('/api/w/{wid}/diy/events/{eid}/plan')
 def save_plan(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or not payload or set(payload)-{'planning_style','theme','notes'}:
   raise HTTPException(422,'Invalid plan settings')
  style=payload.get('planning_style','balanced')
  theme=payload.get('theme','')
  notes=payload.get('notes','')
  if style not in ('budget_first','balanced','experience_first') or not isinstance(theme,str) or len(theme)>160 or not isinstance(notes,str) or len(notes)>2000:
   raise HTTPException(422,'Invalid planning preference')
  with db() as con:
   access(request,con,wid,True);check_event(con,wid,eid)
   con.execute('''INSERT INTO diy_event_plans(event_id,workspace_id,planning_style,theme,notes,updated_at)
    VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
    planning_style=excluded.planning_style,theme=excluded.theme,notes=excluded.notes,updated_at=excluded.updated_at''',
    (eid,wid,style,theme.strip(),notes.strip(),now()))
  return {'ok':True}

 @app.put('/api/w/{wid}/diy/events/{eid}/allocations')
 def save_allocation_json(request:Request,wid:str,eid:str,payload:dict):
  # Category names can contain punctuation and must not be URL path segments.
  if not isinstance(payload,dict) or set(payload)!={'category','target'}:
   raise HTTPException(422,'Choose a budget category and target amount')
  category=payload['category']
  if not isinstance(category,str) or category not in CATEGORIES:
   raise HTTPException(422,'Invalid budget category')
  return save_allocation(request,wid,eid,category,{'target':payload['target']})

 @app.put('/api/w/{wid}/diy/events/{eid}/allocations/{category}')
 def save_allocation(request:Request,wid:str,eid:str,category:str,payload:dict):
  if category not in CATEGORIES or not isinstance(payload,dict) or set(payload)!={'target'}:
   raise HTTPException(422,'Invalid budget category')
  amount=valid_amount(payload['target'])
  with db() as con:
   access(request,con,wid,True);check_event(con,wid,eid)
   con.execute('''INSERT INTO diy_budget_allocations(event_id,workspace_id,category,target,updated_at)
      VALUES(?,?,?,?,?) ON CONFLICT(event_id,category) DO UPDATE SET
      target=excluded.target,updated_at=excluded.updated_at''',(eid,wid,category,amount,now()))
  return {'ok':True}

 @app.post('/api/w/{wid}/diy/events/{eid}/suggest-allocations')
 def suggested_allocations(request:Request,wid:str,eid:str):
  with db() as con:
   access(request,con,wid,True)
   ev=check_event(con,wid,eid)
   existing=con.execute('SELECT COUNT(*) FROM diy_budget_allocations WHERE event_id=? AND workspace_id=? AND target>0',(eid,wid)).fetchone()[0]
   if existing:raise HTTPException(409,'Category targets already set. Clear them before applying a new suggestion.')
   from decimal import Decimal,ROUND_HALF_UP
   goal=Decimal(str(ev['budget'] or 0))
   weights=BUDGET_WEIGHTS.get(ev['type'],BUDGET_WEIGHTS['Other Event'])
   allocated=Decimal('0.00')
   for i,(cat,weight) in enumerate(zip(CATEGORIES,weights)):
    amount=(goal*Decimal(weight)/100).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP) if i<len(CATEGORIES)-1 else goal-allocated
    allocated+=amount
    con.execute('''INSERT INTO diy_budget_allocations(event_id,workspace_id,category,target,updated_at)
      VALUES(?,?,?,?,?) ON CONFLICT(event_id,category) DO UPDATE SET
      target=excluded.target,updated_at=excluded.updated_at''',(eid,wid,cat,float(amount),now()))
  return {'ok':True,'categories':len(CATEGORIES),'note':'Editable planning example, not a vendor price estimate.'}

 @app.post('/api/w/{wid}/diy/events/{eid}/starter-tasks')
 def starter_tasks(request:Request,wid:str,eid:str):
  with db() as con:
   access(request,con,wid,True)
   ev=check_event(con,wid,eid)
   todo=TEMPLATES.get(ev['type'],TEMPLATES['Other Event'])
   existing={r['title'] for r in con.execute('SELECT title FROM tasks WHERE workspace_id=? AND event_id=?',(wid,eid))}
   count=0
   for title in todo:
    if title in existing:continue
    con.execute('INSERT INTO tasks(id,workspace_id,event_id,title,due_date,priority,status,assigned_to) VALUES(?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()),wid,eid,title,'','Medium','Open',''))
    count+=1
  return {'ok':True,'created':count,'template_count':len(todo)}
