# Demo images for the judging walkthrough

| File | Source | Licence |
|------|--------|---------|
| chest_pneumothorax.jpg, chest_normal.png | Wikimedia Commons (Pneumothorax_CXR.jpg, Chest_Xray_PA_3-8-2010.png) | CC BY-SA / public domain as published on Commons |
| leg_fracture.jpg | Fracture-detection project test image bundled with this repository | project data |
| skin_lesion.jpg | sample upload from the skin-lesion project | project data |

Synthetic / public images only. No patient data.

| derm_case1_melanoma.jpg | ISIC Archive image ISIC_0000036 (dermoscopic, histology: invasive melanoma) | CC-0 (public domain), attribution: Anonymous / ISIC |
| derm_case2_nevus.jpg | ISIC Archive image ISIC_0000007 (dermoscopic, nevus) | CC-0 (public domain), attribution: Anonymous / ISIC |

The two ISIC images are the dermoscopic inputs for the Dermatology outpatient cases
(`scripts/seed_dermatology_cases.py`). They were chosen after running the local
skin-lesion ensemble on 20 CC-0 candidates; the model's own output on them is
melanoma (~98 %) and nevus (~99.9 %) respectively — nothing is hard-coded.
