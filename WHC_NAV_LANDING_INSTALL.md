# EventOS WHC staging — navigation & landing-page update

## What this patch changes
- A size-constrained uploaded brand logo in EventOS navigation and public pages. The full name remains visible next to the logo as space allows.
- Clickable, hierarchical breadcrumbs and a step-back button for EventOS views; browser Back remains available.
- Conversion-oriented Alberta-first landing page with DIY planning, vendor comparisons, invitations, community and business CRM value propositions.
- `/for-vendors` public landing page explaining vendor listing benefits, reviews, quotes, verification and bookings.
- Session-aware public navigation: signed-out users see Login/Start planning; signed-in users see Dashboard/My Account/Sign out. Session is checked server-side via `/api/me`.
- A public, read-only `/api/public/site-brand` endpoint for the configured logo and favicon. SEO and hero settings remain admin-managed.

## WHC deployment (staging first)
1. Back up the whole `eventos_next` directory, its uploads, and the **actual staging MariaDB database** using WHC/cPanel/JetBackup. Confirm backup availability before continuing.
2. In **cPanel > File Manager > eventos_next**, upload `EventOS-WHC-Navigation-Landing-Patch.zip` and choose **Extract here**. Preserve folder structure, and replace only the included application files. (The ZIP contains an `app.py`, a `platform_studio.py`, and files under `static/`.) Do not extract into `public_html` and do not delete any directories.
3. Open **cPanel > Setup Python App > eventos_next** and click **Restart**. Do not touch the live application's root or environment variables.
4. Test `https://staging.honestlyzen.com/`, `https://staging.honestlyzen.com/for-vendors`, `https://staging.honestlyzen.com/discover/vendors`, and `https://staging.honestlyzen.com/app`.
5. As a guest, verify the public navigation shows Log in and Start planning. As a signed-in user, revisit the public homepage and verify Dashboard, My Account and Sign out. Test a breadcrumb chain such as Dashboard > Events > Event > Guests & RSVP and its step-back action.
6. From Platform Admin > Website & SEO, upload a square and a wide logo in turn; ensure neither stretches the public header or sidebar. Confirm SEO title and hero copy remain editable.
7. Check the page at desktop and iPhone widths, including logged-in menu; keep this deployment on staging until tests pass.

## Safety and rollback
- This patch does **not** change MariaDB tables or seed/delete records. **NO setup script is needed.**
- Use the saved file backup to restore these files if problems occur; restart the staging Python app. No database restore is required unless another process changed the data.
- Credentials do not belong in screenshots or the ZIP. For security, rotate previously exposed MariaDB passwords before production launch.
- CTA buttons point to account signup or EventOS; they do not automatically enroll vendors or guarantee leads.
