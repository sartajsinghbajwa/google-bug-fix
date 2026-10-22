# EventOS Growth Center — release notes

Cumulative release built on EventOS-WHC-PartyHub-DIY-All-In-One. Retains existing admin, accounts, support, knowledge base, Alberta Party Hub and DIY planning features.

## New features

- **System-wide in-app notifications:** notification bell, unread indicator, mark-as-read history, audience targeting (all / personal / business / vendors), admin announcements, optional expiry, private vendor negotiation notifications. Checks every 60 seconds while the tab is active; no mobile push notifications or email delivery are claimed.
- **Urgent alert banners:** administrator creates information, warning or critical alerts; scoped by audience, optional end date; shown in authenticated app. Alerts sent to the `all` audience also appear on public pages.
- **Blog/news CMS:** public `/news`, searchable/category-filtered article pages, drafts, editorial publication, title/summary/cover images, text articles, editing and unpublishing; only platform admins can edit. No user-submitted HTML is rendered.
- **Vendor dashboard:** owned listings, publication and verification states, quote counts, accepted/booked quote counts, moderated review summaries and average ratings, direct links to negotiations and listing edits. No payment balance or external accounting feed is implied.
- **Vendor media:** one photo upload per listing, 3 MB max input; JPG/PNG/WebP converted to WebP and stripped of metadata. Uploaded photo changes return listings to approval queue. One supported video link (YouTube / Vimeo) per listing; videos open in a new tab rather than accepting arbitrary embed code. Local uploaded photos need file backups / persistent storage.
- **Google Analytics 4 / Meta Pixel IDs:** admin-only settings, server-validated public measurement IDs, optional visitor opt-in consent. Tags are not loaded before consent; staging should use test IDs. Internal administrative counts are not Google Analytics reports or Meta ad metrics. Google and Meta accounts are not connected automatically.

## Additive database changes

**Five new tables:** growth_notifications, growth_notification_reads, growth_alerts, growth_articles, growth_tracking. One additional `video_url` column on market_listings. No DROP/DELETE, existing users and listing content kept.

The standalone cPanel migration filename is `EventOS-prepare-Growth-Center-MariaDB.py`.

## Verification and limitations

Local testing confirms core authorization boundaries, article drafts/publication, notification audiences, admin restrictions, vendor ownership, one-image uploads, existing Party Hub and DIY planner integration, and JavaScript syntax/rendering. WHC-specific MariaDB execution and cPanel Python App restarts are not tested here. Check performance, Safari, email and payment integrations separately before a live rollout.

**Privacy:** provide a Canadian-compliant privacy/cookie policy, make consent and preference changes discoverable, verify third-party tags with your own GA4 and Meta test accounts, and assess PIPEDA and relevant provincial legal obligations before using ad tracking publicly. Consent management here is a first-release browser control, not a legal compliance certification.

## Email notification extension (new)

See `EMAIL_NOTIFICATIONS_RELEASE_NOTES.md`. New `email_notification_preferences` and `email_notification_outbox` tables. Use **`EventOS-prepare-Growth-Email-MariaDB.py` instead of** the older Growth-only migration when deploying this release. SMTP sending is disabled until configured later. Does not store credentials or mail guests without a direct invitation workflow.
