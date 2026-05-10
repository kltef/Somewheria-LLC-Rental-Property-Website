# Browser Support

This page records the browsers the Somewheria rental site is expected to work on, the rationale for what we drop, and the manual QA checklist a human walks through to sign off a release on real devices.

The site uses Tailwind 3 and modern (ES2017+) JavaScript with no transpilation step. There is no automated cross-browser test harness — sign-off requires a human running the checklist below.

## Supported browsers

We target the **latest stable release plus one prior major version** of each of the following:

| Browser           | Platform(s)         | Notes                                                       |
|-------------------|---------------------|-------------------------------------------------------------|
| Safari            | macOS               | Test latest and N-1.                                        |
| Safari            | iOS / iPadOS        | Test on a real device — the simulator misses touch quirks.  |
| Google Chrome     | Windows, macOS, Linux, Android | Primary development browser.                     |
| Microsoft Edge    | Windows, macOS      | Chromium-based; Edge-specific quirks usually around PWA install. |
| Samsung Internet  | Android             | Significant share on Samsung devices; check the menu and theme toggle. |
| Mozilla Firefox   | Windows, macOS, Linux | Test latest and N-1.                                      |

## Explicitly unsupported

**Internet Explorer (any version).**

Rationale:

- The site ships untranspiled modern JavaScript (template literals, `const`/`let`, arrow functions, `fetch`, `Headers`, optional chaining in some inline scripts) which IE does not run.
- Tailwind 3 uses CSS custom properties and modern selectors that IE does not render correctly.
- Internet Explorer is end-of-life. See Microsoft's official IE end-of-life announcement.

Users on IE will see a broken page. The footer carries a brief recommendation to upgrade.

## QA checklist

For each release that touches templates, JavaScript, CSS, or auth flows, walk through every row on every supported browser. Fill in `PASS` or `FAIL` (with a short note) in the cell. A row failing on one browser does not block the others — record it and triage.

**Test scenarios (one row per scenario):**

1. **Home (`/`)** — page renders, hero image loads, header/footer visible, no console errors.
2. **For Rent (`/for-rent`)** — listings grid loads, thumbnails render, filters/search behave, no layout shift after images load.
3. **Property details (`/property/<uuid>`)** — pick a real listing UUID, verify gallery, address, description, and the "request viewing" CTA all render.
4. **Login + Google OAuth (`/login` -> Google -> callback)** — clicking "Sign in with Google" reaches the Google consent screen and returns the user to the app authenticated.
5. **Admin dashboard (`/admin/dashboard`)** — sign in as a high_admin, confirm panels render, links work, no JS errors.
6. **Edit listing (`/edit-listing/<id>`)** — open an existing listing, change a field, submit, confirm the change persisted.
7. **Upload image (`/upload-image/<uuid>`)** — upload a JPEG and a PNG, confirm both appear on the property after refresh.
8. **New ticket with file upload (`/tickets/new`)** — create a ticket and attach a small image; confirm it lands in `/admin/tickets`.
9. **Mobile menu open/close** — viewport <= 640px (or real phone): hamburger opens, body scroll locks, tapping an item closes the menu, Esc closes it on desktop-with-narrow-window.
10. **Theme toggle** — click the sun/moon button: theme switches, preference persists across reload, system theme change is honored when no explicit preference is set.

**Matrix — fill in PASS / FAIL per cell:**

| #  | Scenario              | Safari (macOS) | Safari (iOS) | Chrome | Edge | Samsung | Firefox |
|----|-----------------------|----------------|--------------|--------|------|---------|---------|
| 1  | Home                  |                |              |        |      |         |         |
| 2  | For Rent              |                |              |        |      |         |         |
| 3  | Property details      |                |              |        |      |         |         |
| 4  | Login + Google OAuth  |                |              |        |      |         |         |
| 5  | Admin dashboard       |                |              |        |      |         |         |
| 6  | Edit listing          |                |              |        |      |         |         |
| 7  | Upload image          |                |              |        |      |         |         |
| 8  | New ticket + upload   |                |              |        |      |         |         |
| 9  | Mobile menu           |                |              |        |      |         |         |
| 10 | Theme toggle          |                |              |        |      |         |         |

Record the date, release tag (or commit SHA), and tester initials at the top of the filled-in copy. Archive completed sheets in the release record — this file stays as the blank template.

## When to re-run

- Any change to `templates/base.html` (header, footer, theme toggle, mobile menu).
- Any change to inline JavaScript in templates, or to the service worker.
- Tailwind config or `static/css/input.css` changes.
- New form, new file upload, or new auth-gated page.
- Bumping a major dependency that ships client-side code.

Routine backend-only changes (services, routes that return existing templates unchanged, JSON storage tweaks) do not require a full re-run.
