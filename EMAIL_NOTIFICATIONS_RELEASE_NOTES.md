# EventOS Email Notifications — release notes

## What is included

- System-wide *queued* transactional email notifications with a durable outbox, status/retry/lease tracking and idempotency keys.
- No SMTP credentials stored in the app/database; email is disabled until `EVENTOS_EMAIL_NOTIFICATIONS_ENABLED=1` **and** SMTP variables are set in cPanel.
- Event notifications: new events and event status changes; RSVP response updates; marketplace quote requests, negotiation replies and contract acknowledgements; vendor listing and verification moderation; published-review moderation; private community-thread replies (opt-in); support ticket creation, replies and status; security password changes, account signup; verified Stripe subscription changes and verified Stripe Connect invoice payments.
- Audience-targeted platform announcements/alerts are eligible for email **only with recipients' explicit `announcements` opt-in** (default off). In-app alerts work regardless. Avoid bulk announcement fan-out in a single request for audiences larger than 1000 opted-in recipients; use an external email provider/queue for scale.
- Customer preferences in My Account for events, RSVP, vendors, contracts, billing, support, announcements. Security messages are not optional.
- Platform admin can view mail-delivery configuration and queue counts and queue a test message to their own account.
- Delivery worker is `scripts/send_email_notifications.py`, suitable for a WHC cPanel Cron Job when available; no long-running background process, external services, native push or SMS required.

## Setup later, not now

Save the following variables in cPanel Python App for `eventos_next` (never in files committed to the web):

- `EVENTOS_SMTP_HOST` = host given by provider
- `EVENTOS_SMTP_PORT` = `465` (TLS on connect) or `587` (STARTTLS)
- `EVENTOS_SMTP_USER` = verified sending account
- `EVENTOS_SMTP_PASSWORD` = provider's SMTP credential
- `EVENTOS_SMTP_FROM` = verified From address
- `EVENTOS_PUBLIC_BASE_URL` = `https://staging.honestlyzen.com` for staging
- `EVENTOS_EMAIL_NOTIFICATIONS_ENABLED` = `0` (default; change to `1` only when ready)

Once SMTP is configured, turn notifications on and restart the staging Python app. In the admin Growth Center, queue an SMTP test; run `scripts/send_email_notifications.py` in cPanel's **Execute Python Script** and verify delivery. To automate, set a cPanel Cron Job that runs the Python virtualenv interpreter and this script every 5–15 minutes (subject to WHC shared-hosting cron limits). Confirm the virtualenv Python path in **Setup Python App**, and ensure the worker executes under the same cPanel environment variables; Cron may not load those automatically. If Cron doesn't load the application variables, use a *private*, owner-readable wrapper that loads only the necessary settings from an outside-web-root configuration file (not public_html). Never put passwords in cron command strings or screenshots.

**Before enabling**: SMTP alone isn't sufficient—the cron/worker must also be configured; otherwise outbox records accumulate. Never enable delivery before testing cron environment, sender identity, DNS SPF/DKIM/DMARC and message volume.

## Deployment

1. Take WHC JetBackup backup of both staging files and MariaDB.
2. Extract the all-in-one ZIP **directly** into the existing `eventos_next` app root. Do not delete the existing database.
3. In cPanel **Execute Python Script**, run `EventOS-prepare-Growth-Email-MariaDB.py` **once**, **before restarting**. This covers the previous Growth Center schema plus 2 new email tables (7 additive tables total); it also checks/adds the existing vendor video-link column. It assumes the Account, Party Hub and DIY migrations have already run.
4. Restart `eventos_next`. Confirm `/api/health`, admin pages, My Account email toggles and existing features. Email remains off.
5. Configure SMTP later as above, then test with a test mail and verify queue statuses. Do not switch the live site until WHC tests are complete.

## Delivery guarantees and limitations

- Outbox records are committed with the related database change. SMTP failures are retried up to 5 attempts with delay, with error *type* logged (not credentials).
- Sending is *at least once*; a crash after SMTP accepts a message can occasionally cause a duplicate. Keep messages informative but without secrets or personal guest details.
- No mail is queued while delivery is off, so enabling later **does not send historical notifications**.
- External verified-email, password-reset and email-change flows already require SMTP and remain separate, time-sensitive transactional sends.
- A standard SMTP mailer does not manage unsubscribes or marketing consent compliance automatically. Platform announcements default to opt-out until recipients opt in. Review applicable CASL/privacy obligations before live Canadian marketing campaigns.
- Email deliverability, real WHC MariaDB behavior, and actual SMTP/cron execution must be verified on WHC staging; local tests mock SMTP.
