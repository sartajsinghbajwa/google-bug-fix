# EventOS WHC fixes: budget, invitations, fonts and image uploads

## Changes

- DIY category budget save now uses `PUT .../allocations` with a JSON body `{ "category": "Food & drinks", "target": 123.45 }`, avoiding URL encoding issues with `&` and spaces. Legacy category-in-URL route retained for old clients.
- Invitation save now has a 20-second request timeout, clear failure feedback and a `finally` redraw so the button never remains in the "Saving…" state indefinitely. It does not remove or replace existing invitations or RSVP records.
- Includes ten curated Google Fonts for canvas and builder invitation designs, displayed in the invitation picker and allowed by backend validation. Google Fonts require client connectivity to Google's font service; uploaded custom font files remain private to the workspace's authorized font library.
- Only **platform administrators** who are authorized members of a workspace can upload/delete custom font files; workspace owners can still manage clipart. All event participants can use approved Google Fonts and available library fonts.
- Platform website logo, favicon and social-preview image can be uploaded (JPG, PNG or WebP) from Platform Admin. Blog covers also use upload controls. Files are re-encoded to WebP and EXIF stripped, stored in the database; an image becomes public only if referenced in live website settings or a published blog article.
- Vendor image can be uploaded while creating the listing or changed afterward. Invitation backgrounds, agency logos and registry photos already had upload support; legacy registry/cover image URLs are retained in the database for backward compatibility, but URL entry controls are removed from the main editors.
- No automatic database cleanup: unused uploaded website/blog images remain stored. Plan storage quotas and orphan cleanup before large-scale public production rollout.

## Safety / limitations

- Requires the *one-time* `EventOS-prepare-Image-Font-Fixes-MariaDB.py` database setup AFTER extraction and BEFORE using image upload.
- This package assumes the cumulative Growth + Email release (and previous Party Hub / DIY schema) is installed in staging MariaDB.
- Preserve existing MariaDB database and uploaded files through a complete backup before extracting.
- No live WHC or actual iPhone Safari upload test has been performed yet. Existing Media features and payment flows require ongoing staging QA.
- New Google Fonts are external hosted resources; availability and privacy requirements should be reviewed before production launch.
