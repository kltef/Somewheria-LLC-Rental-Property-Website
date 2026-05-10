# Documentation

Index of documentation for the Somewheria LLC Rental Property Website.

## Pick by audience

| You are… | Read |
|---|---|
| A site admin or high admin who needs to manage listings, users, contracts, or tickets | [ADMIN_GUIDE.md](ADMIN_GUIDE.md) |
| On-call or running the deployment | [RUNBOOK.md](RUNBOOK.md) |
| A developer contributing code | [DEVELOPING.md](DEVELOPING.md) |
| Looking for the HTTP endpoint reference | [API.md](API.md) |
| Signing off cross-browser support before a release | [BROWSER_SUPPORT.md](BROWSER_SUPPORT.md) |
| Setting up the app for the first time | [../README.md](../README.md) |
| Looking for the high-level architecture in one page | [../CLAUDE.md](../CLAUDE.md) |
| Configuring Google OAuth | [../GOOGLE_OAUTH_SETUP.md](../GOOGLE_OAUTH_SETUP.md) |

## What's where

- **[ADMIN_GUIDE.md](ADMIN_GUIDE.md)** — Task-oriented, non-technical. Sign-in, listings (add/edit/upload/delete/toggle-sale), approving pending registrations, managing user roles, contracts, repair-ticket triage, dashboards and analytics.
- **[RUNBOOK.md](RUNBOOK.md)** — Operations reference. Crash-handler behavior, what to do when the AWS properties API is down, restart procedure, rotating secrets, log file inventory, role/access internals, in-process rate limits, security headers and CSP.
- **[DEVELOPING.md](DEVELOPING.md)** — Contributor guide. Service-registry pattern, adding routes and services, CSRF requirement, JSON-file persistence model, properties cache, crash-safety contract, test patterns, Tailwind workflow.
- **[API.md](API.md)** — HTTP endpoint reference. Every route grouped by audience, with auth level, CSRF/rate-limit notes, request/response shapes, error pages, and the security headers set on every response.
- **[BROWSER_SUPPORT.md](BROWSER_SUPPORT.md)** — Supported browser matrix (Safari, Chrome, Edge, Samsung, Firefox; IE explicitly unsupported) and the manual QA checklist a human walks through on each browser before a release.

## Repo-root docs

- **[../README.md](../README.md)** — Project overview, tech stack, quick start, environment variables, route summary, how property data flows.
- **[../CLAUDE.md](../CLAUDE.md)** — Architectural digest for AI assistants and humans skimming the codebase.
- **[../GOOGLE_OAUTH_SETUP.md](../GOOGLE_OAUTH_SETUP.md)** — Step-by-step Google Cloud Console setup for the OAuth client.

## Gaps to fill

These belong in `docs/` but aren't written yet — they require information not in the codebase:

- Production hostname, TLS termination, and reverse proxy configuration.
- On-call rotation and escalation paths.
- Backup schedule and restore procedure for the JSON state files.
- Owner of record for the Google OAuth client and the Gmail sender account.
- Screenshots for the admin guide.
