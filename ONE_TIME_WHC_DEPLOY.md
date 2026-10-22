# EventOS DIY Party Hub — one-time WHC / cPanel staging deployment

Deployment target: **staging.honestlyzen.com**, existing Python app root `eventos_next`, Passenger startup `passenger_wsgi.py`, entry point `application`. This release is a **full cumulative EventOS code package**, not an incremental patch. Do not use GitHub or Render.

## 1. Back up first

In WHC JetBackup/cPanel, take a current **staging MariaDB backup** and a copy of the entire `eventos_next` application directory. Verify you can restore them. Do not modify the live EventOS domain. Keep database archives outside public document roots; never place credentials or backups in `public_html`.

## 2. Upload one ZIP

Use cPanel **File Manager → eventos_next → Upload**, select `EventOS-WHC-PartyHub-DIY-All-In-One.zip`, then **Extract directly into `eventos_next`**, overwriting corresponding source files. Verify the folder contains `app.py`, `diy_planner.py`, `party_hub.py`, `account_support.py`, `static/diy_planner.js`, `static/diy_planner.css`, and `EventOS-prepare-DIY-All-In-One-MariaDB.py`. Avoid nesting in an extra subdirectory.

## 3. Add tables without data loss (before restart)

In **Setup Python App → eventos_next**, confirm only the *setting name* `EVENTOS_DATABASE_ENGINE` is set to `mariadb` and that the private `EVENTOS_DATABASE_URL` still points to the existing dedicated staging database. Do not reveal its value in screenshots. In **Execute Python Script**, run **`EventOS-prepare-DIY-All-In-One-MariaDB.py`** **once**. It checks the base tables, checks/creates the four My Account & Support tables and 11 Party Hub tables, then adds **2 DIY tables** (`diy_event_plans`, `diy_budget_allocations`). This is additive—no reset or replacement of existing accounts/events.

Expected result includes: `SUCCESS: 2 DIY planner tables available, 11 Party Hub tables available.` If an error appears, **stop** and retain output. MariaDB DDL can commit partially; don't rerun until the cause is inspected.

## 4. Restart and validate on staging only

Restart **only `eventos_next`**; check `https://staging.honestlyzen.com/api/health` includes `"status":"ok"` and `"database_engine":"mariadb"`. Log in with an existing *staging* personal user. From the sidebar, open **Plan a party · Wizard** and create a temporary test birthday or anniversary event. Verify all 3 steps, starter tasks and **DIY planning hub**. Test the **Budget breakdown**: generate an editable suggested split, adjust category targets, and add a normal EventOS expense with category `Food & drinks`. Verify amounts under Estimated/Committed/Actual/Paid remain distinct.

Use a separate vendor account (on staging) to create a listing, approve it through platform admin, request a quote *linked to the test event*, then return to **Compare vendors**. Confirm price, offer, verification status and reviews are displayed accurately; quote amounts must not increase committed expense totals unless you independently add an expense. Verify no one can view a different workspace's plans/quotes. Retest My Account, Support Center, Help Center, vendor workflows, Super Admin and the invitation editor. Check layout on a physical iPhone.

## 5. Rollback

If testing fails, restore the previous **application files** from the backup, restart the staging Python app, and verify the health check and existing features. Leave the new additive tables unused; **do not drop or recreate the database**. Restore a database only when a verified rollback procedure requires it.

## Security & limitations

Rotate the MariaDB password that was visible in earlier screenshots. The release does not add live billing, third-party binding e-signatures or authenticated vendor payment settlement. Category allocation suggestions are examples and do not represent researched prices. Local automated SQLite tests cannot prove WHC-specific MariaDB operation, payment processing, or iOS camera behavior. Do not enable public signup, payment processing or production mode until separate production readiness tests pass.

## Growth Center and Email Notifications consolidated deployment (latest ZIP)

If you're installing this latest Growth+Email all-in-one release over an EventOS staging app that already has the account/Party Hub/DIY tables, do **not** run the old Growth-only script. Run `EventOS-prepare-Growth-Email-MariaDB.py` **once after extraction and before restart**. It creates 5 Growth Center and 2 email notification tables and checks the vendor video link column. Email remains off until `EVENTOS_EMAIL_NOTIFICATIONS_ENABLED=1`, verified SMTP, and a working scheduled sender are configured. See `EMAIL_NOTIFICATIONS_RELEASE_NOTES.md` for cPanel Cron Jobs instructions and security considerations.
