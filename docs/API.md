# iHIS REST API

Base URL: `/api`. Responses are JSON. The API uses Flask-Login session auth
(login first, then call). Client-side write calls from forms are handled by
the web UI; JSON write endpoints are CSRF-exempt for programmatic use.

## Public (no auth)

| Method | Path           | Description |
|--------|----------------|-------------|
| GET    | `/api/health`  | Service health check |
| GET    | `/api/doctors` | List doctors (`?q=` filters by license) |
| GET    | `/api/specialties` | List specialties |
| GET    | `/api/patients` | List patients (basic/clinical data) |
| GET    | `/api/lab-tests` | Lab test catalog |
| GET    | `/api/imaging-types` | Imaging types |
| GET    | `/api/medications` | Active medications |

## Authenticated (requires login session)

### Appointments
| Method | Path        | Description |
|--------|-------------|-------------|
| GET    | `/api/appointments` | List (optional `?status=`) |
| POST   | `/api/appointments` | Create. Body: `patient_id`, `doctor_id`, `scheduled_at` (ISO 8601), optional `status`, `priority`, `reason` |

### Patients (scoped: patient self, or clinical/admin staff)
| Method | Path                                  | Description |
|--------|---------------------------------------|-------------|
| GET    | `/api/patients/<id>`                  | Patient detail |
| GET    | `/api/patients/<id>/records`          | Medical records |
| GET    | `/api/patients/<id>/prescriptions`    | Prescriptions |
| GET    | `/api/patients/<id>/lab-orders`       | Lab orders + results |
| GET    | `/api/patients/<id>/radiology-orders` | Radiology orders + reports |

### Prescriptions
| Method | Path                      | Description |
|--------|---------------------------|-------------|
| GET    | `/api/prescriptions`      | List (optional `?patient_id=`, `?status=`) |
| POST   | `/api/prescriptions`      | Create (doctors only). Body: `patient_id`, `medication_id`, `dosage`, `frequency`, `duration`, `instructions`, `refills` |

### Orders
| Method | Path                              | Description |
|--------|-----------------------------------|-------------|
| GET    | `/api/lab-orders/<id>`            | Lab order + result |
| GET    | `/api/radiology-orders/<id>`      | Radiology order + report |

### Care coordination
| Method | Path             | Description |
|--------|------------------|-------------|
| GET    | `/api/referrals` | List referrals |
| POST   | `/api/referrals` | Create (doctors only). Body: `patient_id`, `to_specialty`, `reason`, optional `to_doctor_id` |

### Operational
| Method | Path              | Roles               | Description |
|--------|-------------------|---------------------|-------------|
| GET    | `/api/inventory`  | Pharmacist/Admin    | Inventory + low-stock flag |
| GET    | `/api/users/me`   | Any authenticated   | Current user profile |

## Error handling

- `400` invalid/missing request body fields
- `401`/`302` not authenticated (redirect for browser clients)
- `403` authenticated but not permitted
- `404` resource not found

## Example

```
POST /api/prescriptions
Authorization: (session)
{"patient_id": 1, "medication_id": 2, "dosage": "500 mg",
 "frequency": "twice daily", "duration": "30 days"}

→ 201  {"id": 2, "status": "Active"}
```