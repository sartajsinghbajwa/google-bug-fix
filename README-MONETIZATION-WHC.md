# Honestly Zen Event Planning — Monetization Center

**Deployment target:** WHC cPanel / Python 3.11 / MariaDB staging at `staging.honestlyzen.com`.

**Delivery:** additive, staging-only update. It does **not** enable the public production launch or change `honestlyzen.com`.

## Capabilities

- **Platform Admin → Monetization Center**: revenue dashboard, existing subscription plan summary and shortcut, new CAD offers, sponsored listings, lead-credit packages, premium-event entitlements, promotions, signed-booking commissions, internal invoices, audit log and CSV report.
- **Vendor Dashboard → Paid growth & offers**: vendors can request sponsorship and lead packages against their own listings, or premium upgrades for a workspace they own. Creating a request does **not** charge the user or confer paid benefits.
- **Marketplace**: verified, paid and date-active sponsorship campaigns rise to the top of applicable searches, with a visible **Sponsored** label. Pending/unpublished listings remain excluded.
- **Commission rules**: percent and flat cents; one accrual per completed quote with signed contract. Platform admin must mark external payment as collected and provide the verified receipt reference.
- **Lead credits**: only verified, currently active packages; credits delivered against distinct quote IDs belonging to that vendor. No private quote contact information is exposed to other vendors.
- **Promotions**: codes, percent/CAD amount, expiry, redemption caps; redemption recorded when an admin approves payment.
- **Invoices**: internal draft → issued → paid/void workflow, receipt-reference requirement for paid state, authenticated print view and CSV summary.
- **Premium entitlements**: time-bound server-side entitlement lookup for specified feature keys. **Important:** this is the registry/activation layer, not automatic paywall enforcement for all legacy design tools. Don't advertise individual editor tools as exclusive until that editor is gated by the entitlement lookup.

## What this does not do

This is **not a new payment processor**. It does not connect new offers to Stripe Checkout, automatically charge cards, issue refunds, calculate Canadian taxes, send invoices, or initiate vendor payouts. Existing Stripe subscriptions and agency Connect invoices continue to run through the original tested handlers. Amounts displayed by the center are administrator-verified operational records, not reconciled accounting statements or automatic bank settlements.

## Step-by-step installation on WHC

1. Back up staging **MariaDB**, the `eventos_next` application directory, and user media using JetBackup. Keep a separate restorable copy.
2. Open **WHC cPanel → File Manager → eventos_next**.
3. Upload `EventOS-HZ-Monetization-Center-WHC-Patch.zip` and **extract its contents into the application root**, preserving the `static/` subdirectory. When asked, overwrite only the included files. Do **not** delete other files, rename `eventos_next`, or modify `passenger_wsgi.py`.
4. Open **Setup Python App → eventos_next → Execute Python Script** and enter exactly `EventOS-prepare-Monetization-Center-MariaDB.py`.
5. Run it **once**. Expected: `SUCCESS: Honestly Zen Monetization Center database tables ready.` It creates only nine new prefixed tables and their indexes; no existing records are deleted or migrated.
6. **If it prints STOP**, don't restart or rerun. Share only its output, with passwords/database URL cropped.
7. After success, click **Restart** for `eventos_next` in WHC Setup Python App.
8. Sign in with Platform Admin → choose **Monetization Center**. Test pricing, promo, order approval and the report CSV. Sign in as a vendor and open **Paid growth & offers** to verify only their listings are selectable.
9. Confirm public vendor search does not display unpublished listings, and only paid/approved campaigns show the **Sponsored** label.

### If you haven't installed the previous Vendor & Community upgrade

The combined application ZIP includes its source files, but the prior *additive* MariaDB migration for community saves, answer voting and accepted answers must also have been applied before starting the full combined build. Use `EventOS-prepare-Vendor-Community-Upgrade-MariaDB.py` from the combined package only if those tables are missing, and follow its prior prerequisites and STOP precautions.

### Rollback

Stop before changing anything if the old app has been customized directly on WHC. If rollback is needed, restore the backed-up **application files** and restart. The nine new monetization tables can remain unused in MariaDB; do **not** delete them or any of their records. If a paid status was entered during testing, reconcile it manually with actual provider/bank records before considering migration to production.

### Security

- Strict server-side Platform Admin checks for all management endpoints.
- Vendor offer requests validate actual listing ownership or workspace ownership.
- No Stripe keys or other payment credentials are sent to the browser.
- No automatic billing occurs on redirection or purchase request.
- Set WHC staging HTTPS, secure session cookies, off-site backups and separate production environments before a public launch.
- Keep monetary records in CAD cents; don't use this operational revenue report as a tax filing report.
