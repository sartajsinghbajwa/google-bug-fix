# EventOS Help Center — Personal & Business knowledge base

This release adds **93 curated articles in 36 categories** to the existing EventOS account/support bundle. It is part of the **same one-time cPanel deployment**; do not upload earlier Super Admin or Account + Support ZIPs separately.

## Common categories for all accounts
Getting started; My account & profile; Security & privacy; Workspaces & team roles; Packages & subscriptions; Payments & billing; Creating & managing events; Tasks, timelines & budgets; Guest lists & contacts; Invitations & designer; RSVP forms & responses; Seating charts & floor plans; QR codes & event-day check-in; Event websites & publishing; Galleries & media; Gift registries; Vendor directory; Reports & exports; Troubleshooting & mobile; Support Center.

## Personal account categories
Personal event planning; Weddings & celebrations; Families, plus-ones & meals; Personal budgets & checklists; Personal event day.

## Business & agency categories
Business & agency setup; Leads & sales pipeline; Clients, projects & proposals; Contracts & approvals; Client invoices & payments; Stripe Connect onboarding; White-label branding & portals; Staff & vendor coordination; Multi-event operations; Business analytics & reporting; Automation & notifications.

Each category has practical how-to articles or FAQs. The platform includes search across article titles, summaries, steps and keywords; audience/category/content-type filters; mobile reader views; related articles; and contextual ticket drafts. The Help Center is available to signed-in users through the Account navigation and links to existing secure Support Center endpoints. Articles automatically match the workspace mode but users can switch between Personal and Business material.

## Deployment and data protection
- Contains **no new Help Center database tables**. The combined app includes the existing one-time 4-table migration for My Account and Support Center if this bundle has not already been deployed.
- See `ONE_TIME_WHC_DEPLOY.md` in the ZIP. Do not restart before the one-time account/support table setup is complete.
- Existing EventOS records and MariaDB schema are not reset, and payment credentials are not included.
- Help articles are curated static content stored in `static/help_articles.js`; an admin editing/publishing CMS would require a separate feature request.
- Guides describe installed capabilities and note where a feature depends on subscription access, browser or provider setup.
- Before production, validate WHC MariaDB, SMTP, Stripe/Connect and physical iPhone QR-scanning in staging.

## Tests
- 93 articles / 36 categories and uniqueness checks, cross-audience restrictions, XSS escaping and article routing
- Help Center → Support Center category mapping and contextual draft prefill
- Desktop and iPhone-width Chromium smoke checks (search, browse, article reader, no horizontal scrolling)
- Existing Super Admin and My Account / Support tests
