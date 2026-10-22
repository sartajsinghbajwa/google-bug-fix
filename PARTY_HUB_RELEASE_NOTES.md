# EventOS Party Hub — Alberta First

Version: pilot candidate for WHC staging, October 2026.

## Modules in this all-in-one package

- **Vendor Marketplace** with 23 service categories, 19 Alberta cities, other community support, service-area selection, CAD starting/fixed/quote pricing, optional images, admin publication workflow and manually reviewed vendor verification.
- **Vendor-CRM integration:** one-click save into host workspace `vendors` and optional assignment to an existing event's `event_vendors`.
- **Quotes & negotiations:** workspace-scoped RFQs, private threads and counteroffers, explicit accept/decline/cancel, automatic CRM link, and a basic two-party electronic acknowledgement process with an immutable document hash once signed.
- **Reviews:** one review per completed quote by the original host; admin moderation and public published-star summaries.
- **Party Community:** 16 advice topics, 15 event types, system-derived Host / Business Planner / Vendor / Verified Vendor roles, question and answer threads, report and moderation workflow.
- **Trendboard:** admin-authored articles with draft/publish/hide plus editing, event and topic tags and optional https source links.
- **Public discovery page:** `/discover/vendors` linked from the EventOS landing site. Viewing listings does not require login; requesting quotes and all sensitive actions do.
- Retained full prior Super Admin, My Account, Support Center, Help Center, existing event editor, RSVP, seating, agency CRM and payment integrations.

## Schema

Additive preparation: 11 new `market_*` / `party_*` tables, plus four prior account/support tables if missing. All 74 original tables and existing MariaDB records are preserved. No data/reset/seed included.

## Note on trust

Verification statuses are administered by EventOS operators. Typed agreement acknowledgements are not a substitute for legal review or an independent certified signature provider. Booked review labels are host-reported rather than third-party independently verified. Vendor payments and subscription billing remain separate.
