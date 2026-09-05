# Browser Regression Report

## Environment

- Project: `D:\AI in health care\protoproject\iHIS_Project`
- Browser automation: Playwright browser runtime
- Server: local development server on `http://127.0.0.1:5000`
- Viewports exercised: desktop browser and mobile `390x844`

## Browser results

| Area | Result | Evidence |
|---|---|---|
| Public home | PASS | Rendered content and no console errors |
| Login | PASS | Login redirected to the correct role dashboard |
| Health live/readiness | PASS | HTTP 200 and valid JSON |
| Admin sidebar | PASS | 19 visible links, all rendered, no console/network errors |
| Doctor sidebar | PASS | 16 visible links, all rendered |
| Nurse sidebar | PASS | 11 visible links, all rendered after permission filtering |
| Lab sidebar | PASS | 4 visible links, all rendered |
| Radiologist sidebar | PASS | 8 visible links, all rendered |
| Pharmacist sidebar | PASS | 10 visible links, all rendered |
| Physiotherapist sidebar | PASS | 6 visible links, all rendered |
| Dentist sidebar | PASS | 8 visible links, all rendered |
| Receptionist sidebar | PASS | 6 visible links, all rendered |
| Cashier sidebar | PASS | 4 visible links, all rendered |
| Patient sidebar | PASS | 11 visible links, all rendered |
| SuperAdmin sidebar | PASS | 38 visible links, all rendered |
| Patient Overview / 360 | PASS | Patients 9 and clinical patient 5 rendered with visible `<main>` |
| Patient Documents | PASS | Patient account rendered `/patient/documents`; non-patient access no longer raises 500 |
| AI command center | PASS | Rendered with visible main content |
| Tasks, notifications, reports, billing | PASS | Rendered with visible main content |
| Dark mode | PASS | `data-bs-theme` changed from `light` to `dark` |
| Arabic RTL | PASS | `dir="rtl"` after language toggle |
| Mobile sidebar | PASS | Sidebar opened with `.open`; main content remained visible |

## Browser diagnostics

- No blocking JavaScript exceptions were observed on tested pages.
- No failed CSS, JavaScript, image, or XHR requests were observed on tested
  navigation paths.
- Redirects reached the expected role dashboards without loops.
- The initial browser run exposed 403 links in Nurse and Lab menus. Those links
  were generated from broad role membership instead of destination permissions.
  The menu now filters each clinical link using the effective role permissions.
- The initial browser run exposed a 500 at `/patient/documents` for an Admin or
  SuperAdmin without a patient profile. The route now follows the same guarded
  redirect behavior as the patient dashboard.

## Limitations

- The seeded database has no separate `Radiology Technician` role; the
  implemented `Radiologist` role was tested.
- Workflow POST actions were not submitted destructively through the browser.
  Their GET pages and navigation destinations were checked.
- The mobile document width was within a few pixels of the viewport and should
  receive a later visual polish pass if strict zero-overflow is required.
