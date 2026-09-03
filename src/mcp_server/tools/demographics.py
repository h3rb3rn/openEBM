"""
MCP Tool: ebm_validator_demographics

Validates demographic restrictions (age, gender, insurance class) for GOPs.
Patient data is sourced from PostgreSQL; GOP restrictions from Neo4j.

Examples:
  - GOP 04000 (paediatric base fee):          patients up to age 17 only
  - GOP 03360 (geriatric basic assessment):    patients aged 70 and above
  - Many GOPs:                                 GKV only (not PKV)
"""
import logging
from datetime import date, timedelta

import asyncpg

from ..neo4j_client import get_demographic_restrictions
from ..config import get_mcp_settings

logger = logging.getLogger(__name__)
settings = get_mcp_settings()


async def _load_patient_demographics(patient_id: str) -> dict | None:
    """Load patient demographics directly via asyncpg (no ORM overhead)."""
    try:
        # asyncpg DSN must not contain the +asyncpg driver prefix
        dsn = settings.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                """
                SELECT date_of_birth, gender, insurance_type
                FROM patients
                WHERE id = $1 AND is_active = TRUE
                """,
                patient_id,
            )
            if row:
                return dict(row)
        finally:
            await conn.close()
    except Exception as e:
        logger.error("Failed to load patient %s: %s", patient_id, e)
    return None


def _calculate_age(dob: date, treatment_date: date) -> int:
    age = treatment_date.year - dob.year
    if (treatment_date.month, treatment_date.day) < (dob.month, dob.day):
        age -= 1
    return age


async def check_demographics(
    gop_codes: list[str],
    treatment_date_str: str,
    patient_id: str | None = None,
    patient_override: dict | None = None,
) -> dict:
    """
    Validate demographic restrictions for the proposed GOPs.

    Args:
        gop_codes:          List of GOP codes to check.
        treatment_date_str: Treatment date ISO-8601.
        patient_id:         Persistent mode — loads patient record from PostgreSQL.
        patient_override:   Instant mode — inline {date_of_birth, gender, insurance_type};
                            no DB access required.

    Returns:
        {
          "allowed": ["01435"],
          "violations": [
            {
              "code": "04000",
              "reason": "Age restriction: GOP only for patients up to 17 years",
              "restriction_type": "age_max",
              "patient_age": 45
            }
          ],
          "patient_age": 45,
          "patient_gender": "m",
          "patient_insurance": "GKV"
        }
    """
    try:
        treatment_date = date.fromisoformat(treatment_date_str)
    except ValueError:
        return {"error": f"Ungültiges Datum: {treatment_date_str}"}

    patient_data: dict | None = patient_override
    if patient_data is None and patient_id:
        patient_data = await _load_patient_demographics(patient_id)

    if patient_data is None:
        # No patient data available — skip demographic check, allow all GOPs
        logger.warning("No patient data for demographic check — skipping")
        return {
            "allowed": gop_codes,
            "violations": [],
            "warning": "No patient data available — demographic check skipped",
        }

    dob = patient_data.get("date_of_birth")
    gender = patient_data.get("gender")
    insurance_type = str(patient_data.get("insurance_type", "")).upper()

    patient_age = _calculate_age(dob, treatment_date) if dob else None

    restrictions = await get_demographic_restrictions(gop_codes, treatment_date)

    allowed: list[str] = []
    violations: list[dict] = []

    for code in gop_codes:
        restr = restrictions.get(code, {})
        violation_found = False

        # Age restrictions
        if patient_age is not None:
            min_age = restr.get("min_age")
            max_age = restr.get("max_age")
            if min_age is not None and patient_age < min_age:
                violations.append({
                    "code": code,
                    "reason": f"Minimum age restriction: GOP requires at least {min_age} years (patient: {patient_age})",
                    "restriction_type": "age_min",
                    "patient_age": patient_age,
                    "required_min_age": min_age,
                })
                violation_found = True
            elif max_age is not None and patient_age > max_age:
                violations.append({
                    "code": code,
                    "reason": f"Maximum age restriction: GOP only up to {max_age} years (patient: {patient_age})",
                    "restriction_type": "age_max",
                    "patient_age": patient_age,
                    "required_max_age": max_age,
                })
                violation_found = True

        # Gender restrictions
        required_gender = restr.get("gender")
        if required_gender and gender and required_gender != gender:
            violations.append({
                "code": code,
                "reason": f"Gender restriction: GOP only for gender '{required_gender}' (patient: '{gender}')",
                "restriction_type": "gender",
                "required_gender": required_gender,
            })
            violation_found = True

        # Insurance class restrictions
        allowed_insurance = restr.get("insurance_types", [])
        if allowed_insurance and insurance_type and insurance_type not in allowed_insurance:
            violations.append({
                "code": code,
                "reason": f"Insurance restriction: GOP only for {allowed_insurance} (patient: {insurance_type})",
                "restriction_type": "insurance_type",
                "allowed_insurance_types": allowed_insurance,
            })
            violation_found = True

        if not violation_found:
            allowed.append(code)

    return {
        "allowed": allowed,
        "violations": violations,
        "patient_age": patient_age,
        "patient_gender": gender,
        "patient_insurance": insurance_type,
        "treatment_date": treatment_date_str,
    }
