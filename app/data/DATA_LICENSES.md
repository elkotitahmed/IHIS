# Reference data shipped with iHIS

| File | Source | Licence | Notes |
|------|--------|---------|-------|
| `icd10cm_2026.tsv` | CDC/NCHS, ICD-10-CM FY2026 code descriptions (`ftp.cdc.gov/pub/health_statistics/nchs/publications/ICD10CM/2026/`) | US Government work, public domain | 74,719 codes, code + description only. Rebuilt by `scripts`-style builder from the official zip. |
| `ddinter.tsv` | DDInter, Xiong et al., Nucleic Acids Research 2022 (`ddinter.scbdd.com`) | **CC BY-NC-SA 4.0** — non-commercial use with attribution | 160,235 drug pairs with a severity level (Major / Moderate / Minor / Unknown). Used as a *reference* layer under the local formulary interactions; suitable for an academic / demonstration deployment. A commercial deployment must replace it with a licensed source. |

Neither file contains patient data.
