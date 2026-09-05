# OPEN-SOURCE VALUE SCORE

Cost/benefit scoring of every candidate feature harvested from the open-source benchmark,
per the master-prompt scoring bands (0–10) and prioritization (patient safety > clinical
workflow > operational efficiency > interoperability > UX > visibility > auditability).

Score = impact × need / effort. Values are the author's engineering judgment at
2026-09-05.

## Feature harvest (all natively reimplemented)

| Feature | Safety (0–10) | Workflow (0–10) | Efficiency (0–10) | Interop (0–10) | Effort (S/M/L) | Score | Verdict |
|---------|---------------|-----------------|-------------------|----------------|----------------|-------|---------|
| Structured allergies + safety strip (all pages) | 10 | 8 | 6 | 6 | M | 8.1 | ✅ P1 |
| Reusable patient header/banner | 9 | 8 | 6 | 5 | S | 8.0 | ✅ P1 |
| Clinical inbox + result acknowledgement | 9 | 9 | 8 | 4 | M | 7.8 | ✅ P1 |
| Order sets | 7 | 9 | 9 | 5 | M | 7.6 | ✅ P1 |
| Clinical templates (SOAP/structure) | 6 | 9 | 9 | 4 | M | 7.4 | ✅ P1 |
| Reminder / recall engine | 8 | 8 | 7 | 3 | M | 7.2 | ✅ P1 |
| Clinical summary (verified) | 7 | 7 | 6 | 5 | M | 6.8 | ✅ P1 |
| Critical-lab auto-flag + ack log | 9 | 7 | 5 | 3 | M | 6.7 | ✅ P1 |
| Lab worklist polish (specimen/status) | 5 | 8 | 7 | 4 | S | 6.6 | ✅ P2 |
| Expiry/low-stock badges + stock report | 5 | 6 | 8 | 3 | S | 6.0 | ✅ P2 |
| Partial dispensing visibility | 5 | 7 | 7 | 3 | S | 6.0 | ✅ P2 |
| In-patient dashboard widget | 5 | 7 | 5 | 2 | L | 5.2 | ⏳ P3 |
| Observation density (inline ref ranges) | 3 | 6 | 5 | 4 | S | 5.1 | ⏳ P3 |
| FHIR resource view | 4 | 5 | 5 | 8 | L | 5.0 | ⏳ P3 |
| Form-designer DSL | 3 | 6 | 6 | 4 | XL | 4.4 | ❌ defer |
| Micro-frontend workspace | 2 | 4 | 4 | 3 | XL | 2.7 | ❌ reject |
| PACS/LIS/ERP integration | 4 | 6 | 6 | 6 | XL | 4.6 | ❌ reject |

## Rejected items — rationale
- **Micro-frontend workspace architecture** — destroys server-rendered simplicity,
  breaks PythonAnywhere deployment, adds security/complexity for zero clinical gain in a
  country-wide EMR (SSA). ✅ intentionally rejected.
- **Form-designer DSL** — native forms already cover every specialty workflow; a DSL is
  a meta-project with high maintenance cost and clinical risk. Deferred unless external
  extensibility demand appears.
- **PACS/LIS/ERP integration** (Bahmni backend stack) — out of iHIS scope (SSA context,
  facility-level, self-contained). Not a good fit; costs exceed value.

## Completed-result tracking
| Deliverable | Docs placement | Status |
|-------------|----------------|--------|
| Order sets | `FEATURE_GAP_MATRIX.md`, benchmark §1 | shipped |
| Clinical templates | `OPENEMR_GAP_ANALYSIS.md` | shipped |
| Clinical inbox + ack | `OPENEMR_GAP_ANALYSIS.md` | shipped |
| Reminder engine | `OPENEMR_GAP_ANALYSIS.md` | shipped |
| Patient header/allergy strip | benchmark §1 | shipped |
| Clinical summary | benchmark (chart review) | shipped |

**Conclusion:** the P1 targets each add ≥0.7 value per unit effort with high safety
weighting; the rejects each allocate effort to non-core goals. The harvest is
value-positive and license-clean.