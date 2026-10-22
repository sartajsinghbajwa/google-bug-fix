"""Safe content-removal controls for WHC MariaDB and SQLite.

Visible content is withdrawn; compliance, invoice, booking and audit history are retained.
"""
from fastapi import Request, HTTPException

def install(app,db,get_user,access,check_event):
    def platform_admin(request,con):
        user=get_user(request,con)
        if user['platform_role']!='admin':raise HTTPException(403,'Platform admin required')
        return user

    @app.delete('/api/w/{wid}/events/{eid}')
    def archive_event(request:Request,wid:str,eid:str):
        with db() as con:
            access(request,con,wid,True)
            event=check_event(con,wid,eid)
            if event['status']=='Cancelled':return {'ok':True,'action':'already_archived'}
            con.execute("UPDATE events SET status='Cancelled' WHERE id=? AND workspace_id=?",(eid,wid))
            return {'ok':True,'action':'archived','message':'Event archived. Guest and billing records retained.'}

    @app.delete('/api/platform/users/{user_id}')
    def deactivate_user(request:Request,user_id:str):
        with db() as con:
            actor=platform_admin(request,con)
            if actor['id']==user_id:raise HTTPException(409,'You cannot deactivate your own admin account')
            row=con.execute('SELECT id,platform_role FROM users WHERE id=?',(user_id,)).fetchone()
            if not row:raise HTTPException(404,'User not found')
            if row['platform_role']=='admin':raise HTTPException(403,'Admin accounts must be handled through account recovery')
            con.execute('UPDATE users SET is_active=0 WHERE id=?',(user_id,))
            # Existing tokens must not survive an administrative deactivation.
            con.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
            from phase7_billing import audit
            audit(con,actor['id'],'user.deactivated','user',user_id)
            return {'ok':True,'action':'deactivated','records_preserved':True}

    @app.delete('/api/platform/codes/{cid}')
    def revoke_code(request:Request,cid:str):
        with db() as con:
            actor=platform_admin(request,con)
            row=con.execute('SELECT id FROM saas_codes WHERE id=?',(cid,)).fetchone()
            if not row:raise HTTPException(404,'Code not found')
            con.execute('UPDATE saas_codes SET is_active=0 WHERE id=?',(cid,))
            from phase7_billing import audit
            audit(con,actor['id'],'access_code.revoked','access_code',cid)
            return {'ok':True,'action':'revoked','history_preserved':True}

    @app.delete('/api/growth/admin/{kind}/{item_id}')
    def withdraw_content(request:Request,kind:str,item_id:str):
        table={'notifications':'growth_notifications','alerts':'growth_alerts','blog':'growth_articles'}.get(kind)
        if not table:raise HTTPException(404,'Content type not found')
        with db() as con:
            actor=platform_admin(request,con)
            row=con.execute('SELECT id FROM '+table+' WHERE id=?',(item_id,)).fetchone()
            if not row:raise HTTPException(404,'Content not found')
            if kind=='blog':
                con.execute("UPDATE growth_articles SET status='draft' WHERE id=?",(item_id,))
            else:
                con.execute('UPDATE '+table+' SET is_active=0 WHERE id=?',(item_id,))
            from phase7_billing import audit
            audit(con,actor['id'],'content.withdrawn',kind,item_id)
            return {'ok':True,'action':'withdrawn','message':'Content removed from public view; previously sent emails cannot be recalled.'}
