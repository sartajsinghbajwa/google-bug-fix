# EventOS DIY Party Planner — release notes

This consolidated release builds on EventOS Party Hub, account/support, Help Center and advanced platform admin.

## New customer functionality

- **Three-step DIY event wizard:** celebration type, date/location, guests/budget, theme and preferred planning style. Creates a normal EventOS event (respecting quotas) and starter tasks for 6 common event types plus a general template.
- **Planning studio:** event-specific checklist progress with idempotent starter templates; editable personal goals and priorities.
- **Transparent budget dashboard:** set targets across 12 named categories, optionally generate an *editable suggested percentage split* (not market price guidance). Compare target vs budget-item estimates, commitments, actual costs and manually recorded payments. Unallocated funds, overspending and outstanding actual costs are shown clearly.
- **Vendor comparison:** side-by-side display of event-linked marketplace quote requests, latest offer amount, advertised minimum/fixed price, verified badge state, published reviews, status, and direct links to vendor listing/negotiation. Also shows assigned CRM suppliers without counting them twice.
- **Workspace access controls:** all new endpoints require authenticated workspace membership; changes require owner/editor privileges.

## Persistent data

Two additional additive tables: `diy_event_plans`, `diy_budget_allocations`. Existing account support, Party Hub and EventOS core tables are retained. No demo seed or fabricated vendor data is created. The bundled MariaDB setup script checks core tables first and never drops them.

## Financial accuracy

Quotes are not booked expenses, and manually recorded `paid` totals are not Stripe settlement verification. Suggested category percentages are editable starting points, not price forecasts. Existing budget-item categories outside the 12 named buckets still count toward overall totals and are separately listed. The dashboard does not charge users, alter invoices or make booking guarantees.

## Validation and limitations

Local SQLite API integration and JavaScript syntax tests pass. The runtime has **not** been exercised against the actual WHC MariaDB account, iPhone Safari, live payments, or an active production vendor population. Browser automation was unavailable in this sandbox; visually verify both mobile and desktop during WHC staging QA. Keep the existing site backed up and perform cutover only after staging verification.
