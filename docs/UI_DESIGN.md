# iHIS UI v4 — "Calm clinical, AI-forward"

The visual layer that makes the project's identity obvious at a glance:
**a hospital information system with AI built in**, without adding noise
to clinical work.

## Principles

1. **One idea per screen.** Dashboards open with the few numbers that
   matter; everything else is one click away in collapsible sections.
2. **Two accents only.** Green (`--ihis-primary`) is the hospital: primary
   actions, active navigation, verified data. The violet→blue gradient
   (`--ai-grad`) is reserved for AI: the Copilot button, the ✦ spark, AI
   surfaces and AI labels. If it is not AI, it is never purple.
3. **Honest AI.** Every AI tool shows a computed status — `AVAILABLE`,
   `LIMITED` (deterministic part works, AI narrative paused) or
   `COMING SOON` (model weights not installed). Nothing is displayed as
   available unless it is.
4. **Quiet chrome.** Light sidebar, collapsible groups (state remembered per
   browser), a topbar with only search, AI Copilot, tasks and
   notifications. Theme, language, profile and sign-out live in the account
   menu at the bottom of the sidebar.

## Where things live

| Piece | File |
|-------|------|
| Design layer (tokens, sidebar, topbar, AI language, hub, auth/landing) | `app/static/css/ui.css` (loaded after `style.css` and `copilot.css`; nothing in the older files was deleted) |
| Layout: sidebar AI block, collapsible groups, account menu | `app/templates/base.html`; group state in `app/static/js/app.js` (`ihis-nav-groups`) |
| AI Hub page | `app/templates/ai/hub.html`, catalogue in `app/services/ai/hub.py`, routes `/ai/hub` (every signed-in user) and `/ai/ai-dashboard` (legacy, same page) |
| AI strip on dashboards | `doctor/dashboard.html`, `super_admin/dashboard.html`; predictive statuses from `copilot.predictive_catalogue` |
| Landing / login | `app/templates/index.html`, `app/templates/auth/login.html` (demo accounts shown only when `DEBUG` is on) |

## Navigation model

`current_user_menus()` groups links into stable categories
(`command`, `health`, `clinical`, `operations`, `diagnostics`,
`medications`, `specialties`, `work`, `finance`, `administration`, `ai`).
Only the first group is open by default, plus the group that contains the
current page; the `ai` group is rendered separately at the bottom with the
AI accent. For physicians, **PRACTICE** holds the daily essentials
(Patients, Appointments, Clinical Inbox, Lab Results, Clinical Alerts,
Admissions) and **WORK** holds the rest, collapsed.

## AI visual language (reuse these, do not invent new ones)

| Class | Use |
|-------|-----|
| `.ai-spark` (✦) | any AI action, label or title |
| `.ai-btn` / `.ai-btn.ai-btn-soft` | the AI Copilot entry point / secondary AI action |
| `.ai-pill.ai-pill-{ready,limited,limit,off}` | provider status |
| `.ai-tag` | small "AI" tag next to AI-generated content |
| `.ai-surface` | container for AI content (gradient border/tint) |
| `.ai-label.ai-label-{ai,verified,danger,warn,muted}` | inline classification labels |
| `.hub-status-{available,limited,soon}` | capability status on the AI Hub |

Dark mode and RTL are handled by the same tokens; the layout uses logical
properties (`inset-inline-*`, `margin-inline-*`) so Arabic mirrors correctly.

## Verified (2026-09-06)

- 333 unit/integration tests pass; the all-routes smoke walk (2,756
  role/route combinations) finds no white pages or server errors.
- Browser check on the dev server: landing, login, physician dashboard
  (EN + AR/RTL), AI Hub (light + dark), super-admin command center (desktop +
  375 px mobile), patient portal preview.
