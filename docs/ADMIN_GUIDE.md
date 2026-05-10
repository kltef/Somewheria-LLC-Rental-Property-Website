# Admin Guide

A task-oriented guide for site admins of the Somewheria rental property website. No technical background required.

## Signing in

1. Go to the site and click **Login** (top right).
2. Click **Sign in with Google** and authorize with your Google account.
3. If your account has admin or high-admin access, you'll see additional menu items after sign-in.

If you sign in and don't see admin tools, your Google account isn't on the admin list. Contact whoever manages the deployment to add your email.

## Roles in plain English

- **Renter** — signed-in tenant. Can view their own contracts, profile, and tickets.
- **Admin** — manages listings, registrations, contracts, and tickets.
- **High admin** — everything an admin can do, plus the dashboard, analytics, system status, and user management. Only a high admin can promote someone to admin or high admin.

You can never modify your own account from the user-management page, and you can't assign a role at or above your own. This is on purpose — it prevents accidental self-lockout and prevents an admin from promoting themselves.

## Listings

### Add a new listing

1. Go to **Manage Listings** (or directly to `/manage-listings`).
2. Click **Add Listing** (`/add-listing`).
3. Fill out the form: name, address, rent, deposit, bedrooms, bathrooms, lease length, pets allowed, ADA accessibility, blurb (short summary), description (longer), included amenities (checkboxes), and any custom amenities.
4. Click **Save**. You're returned to the listings page and the new property appears in the cache.
5. Open the new listing's edit page to upload photos (see below) — there is no photo upload on the create form.

### Edit an existing listing

1. From **Manage Listings**, click the listing you want to edit.
2. Change any field on the edit form.
3. Click **Save**. Visitors see the change on the next page load.

### Upload photos to a listing

1. Open the listing's edit page (`/edit-listing/<id>`).
2. Use the photo upload control. Allowed file types: JPG, JPEG, PNG, GIF, WEBP. Maximum size: 16 MB per file. Very large or extreme-aspect-ratio images are rejected as a safety measure.
3. The system letterboxes the image at upload time so the displayed aspect ratio is consistent.

### 3D tours

Each listing can optionally embed one third-party 3D virtual tour (Matterport, Kuula, etc.).

1. Open the listing's edit page (`/edit-listing/<id>`).
2. Paste the tour's share URL into **3D Tour URL**. It must start with `http://` or `https://`. Other schemes (e.g. `javascript:`, `data:`) are silently dropped for safety.
3. Click **Save**. The property detail page now shows a **View 3D tour** button on the gallery and an embedded tour below it.
4. To remove the tour, clear the field and save again — the embed disappears.

Self-hosted `.glb` / `.usdz` files are not supported in this iteration; only third-party URL embeds. Only `my.matterport.com` and `kuula.co` are whitelisted by the Content Security Policy. Tours hosted elsewhere will be blocked by the browser even if the URL is saved.

### Delete a listing

1. From **Manage Listings**, find the listing.
2. Click the delete control. The listing is removed from the upstream property database.

This is destructive. There is no undo from the UI.

### Mark a listing as for-sale (toggle)

1. From **Manage Listings**, click the for-sale toggle on a listing.
2. The state flips between for-rent and for-sale.

## Pending registrations

Visitors who want renter access submit the registration form at `/register`. They appear as a pending request that you must approve or reject.

### Approve a registration

1. Go to **Pending Registrations** (`/admin/registrations`).
2. Find the request — it shows the requester's name, email, and reason.
3. Click **Approve**. The user is granted the **renter** role and removed from the pending list. They get an email saying their registration was approved and they can now sign in.

### Reject a registration

1. Same page, find the request.
2. Click **Reject**. The pending request is removed and the requester gets a polite rejection email. They are not granted any role.

There is no "request more info" action — reject and ask the requester to re-submit with more detail if needed.

## User management (high admin only)

`/admin/users` shows everyone with a saved role. From this page you can add a user, change a user's role, or remove a user.

### Add a user

1. Type the email and pick a role (Renter, Admin, or High Admin).
2. Click **Add**.

You can't assign a role at or above your own. As a regular admin, the High Admin option is rejected.

### Change a user's role

1. Find the user in the list.
2. Pick a new role and click **Update**.

You can only modify users whose current role is below your own.

### Remove a user

1. Find the user in the list.
2. Click **Delete**.

The user is recorded as "revoked" rather than fully erased. This is a deliberate safeguard: if their email is also listed in the deployment's environment variables, the revocation prevents them from silently regaining access on next sign-in. To fully restore access later, re-add them through this same page.

You cannot delete yourself.

## Contracts

`/admin/contracts` lists all renter contracts, grouped by renter email.

### Add a contract for a renter

1. Click **Add Contract** (or scroll to the form).
2. Fill in: renter email, property name, start date, end date, status (default **Active**).
3. Click **Save**.

The contract immediately appears on the renter's dashboard the next time they sign in.

### Remove a contract

1. Find the contract under the renter.
2. Click **Delete** next to that specific contract.

If a renter has no contracts left, their grouping disappears from the page.

The download URL field on contracts is a placeholder — there is no file upload for contract documents in the current UI.

## Tickets (repair requests)

`/admin/tickets` shows every ticket. Renters submit tickets at `/tickets/new`; signed-in renters see their own list at `/tickets`.

### Triage a new ticket

1. Open `/admin/tickets`. Use the filter controls to narrow by status, priority, or text search.
2. Click a ticket to open it.
3. Use the controls to change **Status** (open, in_progress, awaiting_parts, resolved, closed, etc.), **Priority** (low, normal, high, urgent), or **Assigned to** (free-text email).
4. Add a note for the renter or for internal record-keeping. Notes appear on the ticket detail page in chronological order.

If the renter has email updates enabled, status changes and notes you add can trigger an email to them. They can opt out per-ticket or globally in their renter profile.

### Quick filters

- `?status=open` shows only open tickets.
- `?priority=urgent` shows only urgent ones.
- `?q=leak` searches title, description, submitter, and property name.

These can be combined: `/admin/tickets?status=open&priority=urgent`.

## Dashboard and analytics

- **`/admin/dashboard`** (high admin) — combined view: site metrics, charts, ticket summary, and the user-management form inline.
- **`/admin/analytics`** (high admin) — request timing, visitor metrics, and per-day chart data over the last 7 days.
- **`/admin/status`** (high admin) — health check for the running process: how many properties are cached, whether the upstream property API is configured, whether Google OAuth and email are configured, and whether the JSON state files exist on disk.

If `/admin/status` shows anything red, talk to whoever runs the deployment — the runbook explains how to fix the common ones.

## Viewing requests (appointments)

Visitors who request to view a property fill out a form on the property page. There is **no UI for viewing requests** in the admin panel. Each request:

1. Sends an email to the admin recipient.
2. Appends to `property_appointments.txt` on the server (the durable record — emails can be lost).

If you suspect you missed a request, ask the deployment owner to send you the relevant lines from that file.

## Things this guide doesn't cover

- Screenshots — recommend adding them to this doc once the visual design is stable.
- Editing the homepage / about / privacy pages — those are HTML templates and require a developer to change.
- Bulk operations (e.g. mass-approving registrations) — not supported in the UI.
- Export of contracts, tickets, or users — not supported in the UI; the underlying JSON files are the source of truth.
