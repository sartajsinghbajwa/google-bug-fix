# Honestly Zen Event Planning — Vendor Dashboard, Approvals and Community Upgrade

Target: the **existing WHC staging app** in `eventos_next` running MariaDB. **Do not install on honestlyzen.com yet.** This is an additive patch on top of the latest Honestly Zen SEO build and includes existing offline-font support in the cumulative ZIP.

## What's new

- Vendor dashboard: clear status KPIs, storefronts, approval status, pending action list, recent quote requests, review feed and one-click navigation to related tools.
- Vendor listings: listing-first management view, visible review/verification status and explanation of approval workflow.
- Platform Admin: **Vendor listing approvals** shortcut, plus `Listing approvals & moderation` in sidebar. Tabs for pending listings, all listings, verification, reviews, community reports, trends; searchable listing rows and approve/hide actions.
- Marketplace: responsive grids, wrapping actions, accessible scrollable navigation; verified with 390px/768px/1440px browser viewports.
- Community: search by text, topic and city; latest activity, newest, most replies, unanswered and saved topics; save/unsave topics, helpful voting on replies, author-selected accepted answers, community topic shortcuts, reporting and moderation.

## One-time safe WHC deployment

1. Create **fresh backups** of the MariaDB staging database and the full `eventos_next` folder (including uploaded media). Ensure you can download the backup. Do not restore over your running database.
2. In cPanel **File Manager → eventos_next**, upload **EventOS-HZ-Vendor-Community-WHC-Patch.zip** and extract it directly into `eventos_next`. Confirm the files inside `static/` replace their earlier versions. **Do not restart yet.**
3. Go to **Setup Python App → eventos_next → Execute Python Script**. Run this exact filename once:

   `EventOS-prepare-Vendor-Community-Upgrade-MariaDB.py`

   Expected result: `SUCCESS: Vendor & Community upgrade database tables are ready.` It will create three additive tables: `party_post_saves`, `party_reply_helpful`, `party_accepted_answers`. Existing records and table definitions are retained. If it reports **STOP**, don't restart or rerun; share only the error type/output (not secrets).
4. When the script reports SUCCESS, click **Restart** for `eventos_next` in **Setup Python App**.
5. In a new browser tab, open `https://staging.honestlyzen.com/app` and hard refresh/clear cached JS, then sign in with a **Platform Admin** account.

## Smoke test checklist

- Platform Admin → **Vendor listing approvals** (or sidebar → **Listing approvals & moderation**): pending listings appear first; find a vendor by name/city/category, approve one test listing.
- Vendor Dashboard: storefront cards and pending/live statuses; open a quote from the recent inquiries list; visit **My listings** to edit one and confirm it returns to approval.
- Marketplace: test filters and cards at desktop and phone widths without sideways page scrolling.
- Party Community: ask a test question, search it, save it, reply from a different account, mark that reply helpful from another account, select the accepted answer as the original author, check saved and unanswered filters; then test Report / admin moderation.

## Rollback

If files fail after deployment, restore **only the application files** from the backup and restart staging. The three new database tables can remain unused; no existing data was modified by the migration. Do **not** drop tables or restore a database over live records without reviewing newly entered data.

## Scope and limitations

- This is a staged release, not a completed public WHC deployment.
- Three additive MariaDB tables are required for new community interactions. No existing vendor/event/customer data is removed.
- Browser tests use local mocked content. WHC/MariaDB deployment must still be verified on staging.
- The vendor dashboard shows recorded quote/booked activity, **not verified income**. An accepted answer is the question author's selection, not a platform guarantee.
- Current admin listing overview retrieves up to 200 listings, pending-first. For a high-volume production site, add server-side pagination and audit logs as a later phase.
