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

## v4.1 — every page on the system, AI workbenches

- **Global polish** (`ui.css`, "v4.1" block): the few remaining Bootstrap-only
  classes (`badge bg-*`, `alert-*`, `table`, bare `h1` in a page header) are
  mapped onto the design system, so pages that were never hand-styled still
  look native.
- **AI workbench pattern** for every model page: `ai/_workbench_hero.html`
  (title, AI tag, computed status, model facts, provider pill, Back / AI Hub)
  and `ai/_image_input.html` (drop zone with preview, optional camera, selected
  patient document). Results use `.verdict` (danger / ok / info), `.compare`
  (original vs AI image), `.meter-row`, `.review-box`, `.kv`.
  Used by Fracture Detection, Tooth Segmentation, Skin Lesion Detection,
  Clinical Alert Engine, Health Insights, Radiology AI, Clinical Pharmacist AI,
  ICD-10 Coding Assistant, Appointment Optimisation, Hospital Analytics.
- **AI strip on every role home** (`_ai_strip.html` + `ai_quick_tools()`):
  Copilot entry plus up to four AI tools the role can reach, with their real
  status. Nursing, pharmacy, lab, radiology, reception, billing, dentistry,
  physiotherapy, admin, admissions.
- **Quota discipline:** legacy pages (Health Insights, AI Patient Summary,
  Lab Interpretation) render the deterministic result on a plain load and call
  Gemini only from an explicit "✦ Explain with AI" click (`?ai=1`). Crawlers,
  smoke walks and page refreshes no longer spend the free quota.

## AI models — verified end to end (2026-09-06)

| Capability | Engine | Check | Result |
|-----------|--------|-------|--------|
| Dermatology (skin lesion) | ResNet-50 + EfficientNet-B0, PyTorch | real photo through `/ai/skin-lesion-detection` | verdict, Grad-CAM served, review form |
| Fracture detection | YOLOv8-nano | fractured-leg X-ray through `/ai/fracture-detection` | "Fracture suspected", annotated image served |
| Tooth segmentation | U-Net (Keras) | panoramic X-ray through `/ai/tooth-segmentation` | mask served, coverage shown |
| Radiology critical findings | rules + local classifier | positive / negated texts, real signed report | flags positive, ignores negation |
| Clinical Copilot | local sections + Gemini | one provider call | narrative returned, cached |
| Clinical alert engine, risk, coding, scheduling, analytics, pharmacist review, dental summary, ICD lookup, inbox priority, patient education | rules | direct service calls | all return results |

One bug found and fixed on the way: Dentistry AI ordered procedures by a
column that does not exist (`created_at` → `performed_at`).

## Retired pages (2026-09-06, agreed with the owner)

Every page was audited for real value. These were removed or consolidated;
old URLs redirect so bookmarks keep working:

| Old page | Why | Now |
|----------|-----|-----|
| `/admin/ai/appointment-optimization` | one threshold (≥ 20 bookings), not AI | `/admin/capacity`: utilisation next 7 days, no-show rate 30 days, rule-based advice |
| `/admin/ai/coding-assistant` | 12-keyword dictionary | EMR ICD-10 lookup; `/ai/medical-coding/<pid>` (Gemini) |
| `/ai/analytics`, `/admin/statistics` | counts only, duplicated | `/reports/statistics` |
| `/ai/summary`, `/ai/diagnosis-support`, `/ai/soap-notes`, `/ai/smart-orders`, `/ai/patient-communication` | duplicated Copilot actions without budget/cache/review | Patient 360 + `?copilot=<action>` deep link opens the Copilot on that action |
| `/ai/radiology/<order>` | only echoed the report | `/ai/copilot/radiology/<order>` |
| `/ai/prescription/<rx>` | completeness check only | pharmacy prescription page (safety context) |
| `/doctor/patients/<pid>/overview`, `/doctor/patients/<pid>/360` | 3 copies of Patient 360 | `/clinical/patient/<pid>` |

Kept on purpose: `/ai/medication-review` (pharmacist deep review), `/ai/rehab`
(physiotherapy progress), `/ai/lab/<order>` and `/ai/health-insights` (explicit
"Explain with AI"), `/care/cases` (now linked from Referrals and the WORK menu).

## Verified (2026-09-06)

- 333 unit/integration tests pass; the all-routes smoke walk (2,756
  role/route combinations) finds no white pages or server errors.
- Browser check on the dev server: landing, login, physician dashboard
  (EN + AR/RTL), AI Hub (light + dark), super-admin command center (desktop +
  375 px mobile), patient portal preview.
