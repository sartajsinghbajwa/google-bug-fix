# EventOS WHC cPanel deployment and rollback

**Build status:** WHC/cPanel **deployment candidate**, not a certified live-payment production release. Python/SQLite API tests, JavaScript syntax tests, SQLite integrity and a MariaDB *dry-run* have been run locally. The package has NOT been run on WHC, against an actual MariaDB server, against Stripe, or on a physical iPhone. Test these before taking live payments. No GitHub or Render steps are necessary.

## Package layout and safe preservation

`EventOS-WHC-cPanel-upgrade.zip` contains the Python application, existing static assets, Passenger `passenger_wsgi.py`, the MariaDB adapter, migration tool, and this runbook. **It intentionally does not contain a database, account passwords, log files or real environment variables.** The separate `EventOS-original-database-backup.zip` contains a read-only copy of the SQLite database from the ZIP you uploaded; it is **not** a seed that should overwrite your live database. Your live WHC database, uploads, and any newer records must be backed up independently before making changes.

The original ZIP contains **two different SQLite snapshots**: `eventos.sqlite3` at the ZIP root has **0 users, 0 events, 0 guests**; the `eventos_production_private/eventos.sqlite3` snapshot has **2 users, 4 events, 0 guests**. Both are preserved unmodified in the separate backup ZIP. Use the private snapshot if you are intentionally restoring those demo records, but verify it against the **latest live WHC database** first. Never deploy either snapshot over a newer database without reconciliation.

## A. WHC prerequisites and backup

1. Confirm with WHC support that your shared-hosting account permits your Python Passenger app **and** this commercial multi-tenant SaaS's database connections, API traffic, storage and payment webhooks. Shared-hosting limits still apply.
2. Back up the entire existing application directory, media files, and current DB with WHC JetBackup; download a second backup off-site. Keep the existing deployment running as a rollback option. Export the current MariaDB via cPanel/phpMyAdmin if already migrated.
3. Create a **new staging subdomain** with SSL, e.g. `test.yourdomain.com`, and a separate **Setup Python App** application root, e.g. `/home/CPANELUSER/eventos_next`. Keep existing production untouched.
4. In cPanel **MySQL Databases / Database Wizard**, create a dedicated **empty** MariaDB database and user. Assign ALL privileges needed for schema creation/migration to that database only. cPanel prefixes database/user names with your account identifier. The usual host is `localhost`, port `3306` (confirm with WHC).
5. Create a separate private directory outside `public_html` for current SQLite database and media backup. Do not put secrets, SQLite database, or `.env` files in public HTML. Upload the code ZIP into the new Python app root and extract it so `app.py`, `passenger_wsgi.py` and `static/` are directly in that root.

## B. Python setup and environment variables

Use the Python version and **virtual environment activation command shown by cPanel Setup Python App**. Python 3.11 or newer is recommended; verify that packages build on the actual WHC server.

```bash
cd /home/CPANELUSER/eventos_next
python -m pip install -r requirements-whc-live.txt
python -m compileall -q .
```

Set these values using **Setup Python App > Environment variables** (example placeholders; don't copy placeholder passwords). URL-encode special characters in the MariaDB URL password (`@`, `/`, `%`, `#`, etc.).

```dotenv
EVENTOS_DATABASE_ENGINE=mariadb
EVENTOS_DATABASE_URL=mariadb://CPANELUSER_eventosuser:URL_ENCODED_PASSWORD@localhost/CPANELUSER_eventosdb
EVENTOS_PUBLIC_BASE_URL=https://test.yourdomain.com
EVENTOS_SECURE_COOKIES=1
EVENTOS_DEMO_SEED=0
EVENTOS_PUBLIC_DEMO=0
EVENTOS_PRODUCTION_MODE=0
EVENTOS_ADMIN_BOOTSTRAP_TOKEN=GENERATE_PRIVATE_RANDOM_TOKEN_OF_AT_LEAST_32_CHARACTERS
```

Create a strong bootstrap token privately with:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(36))'
```

**Do not** place this token or your Stripe keys inside `passenger_wsgi.py` or the upload archive. Environment changes and restarting may be required for Passenger to read new values. Once an administrator is established, unset the bootstrap token and restart the app. The `/api/platform/bootstrap` endpoint only grants administrator privilege while no admin exists.

**Important:** `EVENTOS_PRODUCTION_MODE=0` is required for this WHC candidate. The original EventOS production launch gate requires managed PostgreSQL and a separate audited backup/monitoring setup. This MariaDB bridge has not yet passed the real MariaDB regression and restore tests necessary to change that guarantee. Do not override the gate merely to call the platform production-ready.

## C. Non-destructive database migration (before starting new app)

Use a read-only source SQLite copy that is the **latest** database from your current site, *not the potentially older snapshot uploaded to ChatGPT*. Make sure there are no new writes during final cutover; either use a maintenance window or a consistent live database backup. If there are no users/events in the snapshot, expect their counts to stay at zero.

```bash
# From the app root with its cPanel Python virtualenv active:
python scripts/migrate_sqlite_to_mariadb.py \
  --sqlite /home/CPANELUSER/eventos_private/current.sqlite3 --dry-run

# Only when source counts look correct AND destination MariaDB is empty:
python scripts/migrate_sqlite_to_mariadb.py \
  --sqlite /home/CPANELUSER/eventos_private/current.sqlite3 \
  --apply --confirm-empty-target
```

The script checks SQLite integrity, requires an empty destination and prints source/target table record counts. It never changes the source SQLite file. **Schema migration has only been dry-run locally; apply has not been verified against WHC's MariaDB.** If it fails, keep the original SQLite, delete/recreate the *new staging* MariaDB database, review the error and do not swap DNS or users to the new instance. Do not run the script against a non-empty database.

The new app can automatically add new EventOS settings tables after migration. Inspect its startup logs and query/inspect table row counts again afterward. Perform a real export/restore drill on the migrated database before launch; native JetBackup support does not verify every future restore.

## D. Configure Passenger and smoke-test

Set cPanel **Startup file** to `passenger_wsgi.py` and **Application entry point** to `application`. Ensure the app's domain is mapped to the correct root and SSL certificate.

```bash
# In cPanel press Restart, or if supported:
mkdir -p tmp && touch tmp/restart.txt
python scripts/smoke_whc_wsgi.py
```

Then visit **`https://test.yourdomain.com/api/health`** and open the main application. Create one test admin from a normal account with the private bootstrap token. Visit platform admin and test: SEO/landing edits, favicon/logo URL and robots/sitemap; manual user activate/deactivate, package quota edits, workspace grants, vendor and guest features, RSVPs, invitation editor, seating, websites, client portal and check-in. Confirm record persistence after a cPanel restart. Test user isolation with a second account. Verify uploads survive restart and never expose application secrets.

## E. Stripe subscriptions (test mode first)

In Stripe create recurring monthly/yearly Prices. Configure these values **only in cPanel's private app environment**:

```dotenv
EVENTOS_STRIPE_SECRET_KEY=sk_test_REPLACE_PRIVATELY
EVENTOS_STRIPE_WEBHOOK_SECRET=whsec_REPLACE_PRIVATELY
# Optional defaults; can also set recurring Price IDs in Admin > Packages:
EVENTOS_STRIPE_PRICE_PRO=price_REPLACE
EVENTOS_STRIPE_PRICE_BUSINESS=price_REPLACE
```

Register a Stripe webhook endpoint for **`https://test.yourdomain.com/api/billing/stripe-webhook`** (verify endpoint by inspecting the currently deployed `phase7_billing.py` if customized). Subscribe to invoice/subscription/checkout events used by the app; confirm actual subscription activation and cancellations are driven by verified webhook delivery, never only a browser success redirect. Verify signing, retries, test subscription renewal, duplicate delivery, quota transitions and refund/cancellation effects. Switch to live Stripe keys only after WHC allows commercial use and all payment checks pass.

## F. Agency client invoice payments (separate Stripe Connect path)

This is a different payment flow from **EventOS recurring subscription billing**. Agency invoices are charged directly on the agency's verified Stripe Connect account. Account onboarding and signed account updates must also be configured.

```dotenv
EVENTOS_CONNECT_ENABLED=1
EVENTOS_CONNECT_SECRET_KEY=sk_test_REPLACE_PRIVATELY
EVENTOS_CONNECT_WEBHOOK_SECRET=whsec_REPLACE_PRIVATELY
EVENTOS_AGENCY_LIVE_INVOICES=1
EVENTOS_AGENCY_INVOICE_WEBHOOK_SECRET=whsec_REPLACE_PRIVATELY
```

Configure the existing Connect account webhook per `agency_connect.py` and a **Connect** webhook for **`https://test.yourdomain.com/api/agency/invoice-stripe-webhook`**, including connected-account `checkout.session.completed` and `checkout.session.async_payment_succeeded`. Test with **test-mode credentials only**: connect agency, confirm charges/payouts enabled in provider state, issue invoice, open PIN-protected client portal, start checkout, complete payment, verify webhook records it **once**, resend same event, and confirm client portal balance. Test payment failures, expired Checkout, already-paid invoices, disputed/returned payments and mismatched account IDs. Do not treat a return to the thank-you page as a successful payment. Implement an operator refunds/chargebacks procedure before turning on real money.

## G. iPhone scanning

Under HTTPS in Safari, go to an event > Check-in > Start Camera. Allow rear camera permission and test an actual printed guest QR pass. A pinned external `jsQR` script is fetched from `cdn.jsdelivr.net` when native `BarcodeDetector` is absent. If CDN access is blocked or permissions denied, staff can use the iPhone's Camera app to open the QR link or paste a code manually. Test successful, duplicate, invalid and revoked passes on physical iPhones; do not assume local JavaScript syntax tests prove camera functionality. If strict offline scanner support is required, vendor the dependency as a reviewed local asset first.

## H. Before any customer launch

Require: actual MariaDB migration+full regression pass; exported/imported MariaDB restore; safe backup retention and off-site tested restore; HTTPS and working webhooks; Stripe test then authorized live checkout; account authorization tests, uploaded-file access tests, customer terms/privacy, log/alerting and WHC written approval for multi-tenant SaaS. **This ZIP is a locally tested deployment candidate, not a blanket production certification.**

## Rollback

Do not destroy old app/SQLite during migration. If the staging deployment fails, stop its Passenger process and keep the existing site/DNS unchanged. If live cutover has already happened, disable new writes, restore the last consistent original application and its matching current backup; independently account for transactions after the last backup. Do not merge or overwrite divergent SQLite/MariaDB databases without a verified reconciliation procedure. Keep payment webhooks pointed at the site currently handling payments; never process the same financial webhook in two apps.
