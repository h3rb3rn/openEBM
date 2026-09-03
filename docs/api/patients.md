# Patients API

Base path: `/api/patients`

Requires scope `patients` for API key callers.

---

## GET /

List patients. Supports optional search across last name, first name, and patient number.

```http
GET /api/patients/?search=müller
Authorization: Bearer <token>
```

Response: array of `PatientResponse`.

---

## POST /

Create a patient.

```json
{
  "patient_number": "P-001",
  "first_name": "Max",
  "last_name": "Mustermann",
  "date_of_birth": "1975-04-20",
  "gender": "m",
  "insurance_type": "GKV",
  "insurance_id": "A123456789",
  "insurance_company": "AOK Bayern"
}
```

Returns 201 on success.

---

## GET /{patient_id}

Fetch a single patient. Returns 404 if not found or belongs to a different tenant.

---

## PatientResponse schema

```json
{
  "id": "uuid",
  "patient_number": "P-001",
  "first_name": "Max",
  "last_name": "Mustermann",
  "date_of_birth": "1975-04-20",
  "gender": "m",
  "insurance_type": "GKV",
  "insurance_id": "A123456789",
  "insurance_company": "AOK Bayern",
  "is_active": true
}
```
