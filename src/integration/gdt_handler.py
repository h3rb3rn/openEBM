"""
GDT/BDT Parser und Exporter — German Praxis-PVS Bridge
Standard: GDT 2.1 (KBV-Spezifikation)
Encoding: ISO 8859-1 (Cp1252 kompatibel)
Line format: LLL FFFF content \\r\\n
  LLL  = 3-stellige Gesamtzeilenlänge (inkl. LLL selbst und \\r\\n)
  FFFF = 4-stellige Feldkennung
"""
import base64
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO

logger = logging.getLogger(__name__)

GDT_ENCODING = "iso-8859-1"

# ─── Feldkennungen ────────────────────────────────────────────────────────────

# Patientenstamm
FK_PATIENT_ID       = "3000"  # Patientennummer
FK_NAME             = "3101"  # Nachname
FK_FIRSTNAME        = "3102"  # Vorname
FK_BIRTHDATE        = "3103"  # Geburtsdatum DDMMYYYY
FK_TITLE            = "3104"  # Titel
FK_INSURANCE_NO     = "3105"  # Versichertenummer
FK_STREET           = "3107"  # Straße
FK_ZIP              = "3108"  # PLZ
FK_CITY             = "3109"  # Ort
FK_GENDER           = "3110"  # Geschlecht: 1=m, 2=w, 3=d
FK_INSURANCE_STATUS = "3119"  # Versicherungsart: 1=Mitglied, 3=Familie, 5=Rentner
FK_INSURANCE_NAME   = "4101"  # Name der Krankenkasse
FK_INSURANCE_IK     = "4111"  # IK-Nummer Kostenträger

# Klinische Inhalte
FK_FREE_TEXT        = "6205"  # Freitext (allgemein)
FK_FINDING_TEXT     = "6220"  # Befundtext / klinische Befunde
FK_DIAGNOSIS        = "6227"  # ICD-Diagnose
FK_DIAG_TEXT        = "6228"  # Diagnosetext (Klartext)
FK_TREATMENT_DATE   = "6200"  # Behandlungsdatum DDMMYYYY

# GDT-Metadaten
FK_RECORD_TYPE      = "8000"  # Satzart
FK_GDT_VERSION      = "9218"  # GDT-Version
FK_SENDER_ID        = "8315"  # Sender-ID
FK_RECEIVER_ID      = "8316"  # Empfänger-ID
FK_CHARSET          = "9206"  # Zeichensatz
FK_FILE_VERSION     = "0001"  # Dateiversion

# GDT Satzarten (Record-Types für Feld 8000)
SATZART_PATIENT_MASTER   = "0101"  # Patientenstammdaten übermitteln
SATZART_UNTERSUCHUNG     = "6310"  # Untersuchungsergebnis (Anforderung an Gerät)
SATZART_BEFUND           = "6311"  # Befunddaten (Gerät → PVS)
SATZART_LABORBEFUND      = "6302"  # Laborbefund
SATZART_REQUEST          = "8200"  # Anforderung (PVS → Subsystem)
SATZART_RESPONSE         = "8220"  # Ergebnisse (Subsystem → PVS)

# Geschlecht-Mapping GDT → intern
_GENDER_MAP = {"1": "m", "2": "w", "3": "d", "M": "m", "W": "w", "D": "d"}
# Versicherungsart-Mapping
_INSURANCE_MAP = {
    "1": "GKV",   # Mitglied
    "2": "GKV",
    "3": "GKV",   # Familie
    "5": "GKV",   # Rentner
    "6": "PKV",   # Privat
    "9": "SELBSTZAHLER",
}


# ─── Datenstrukturen ──────────────────────────────────────────────────────────

@dataclass
class GDTPatient:
    patient_number: str = ""
    last_name: str = ""
    first_name: str = ""
    birth_date_raw: str = ""   # DDMMYYYY
    gender_raw: str = ""       # "1", "2", "3"
    insurance_number: str = ""
    insurance_status_raw: str = ""
    insurance_name: str = ""
    insurance_ik: str = ""

    @property
    def date_of_birth(self) -> str | None:
        """Konvertiert DDMMYYYY → ISO YYYY-MM-DD."""
        d = self.birth_date_raw.strip()
        if len(d) == 8:
            try:
                return f"{d[4:8]}-{d[2:4]}-{d[0:2]}"
            except Exception:
                pass
        return None

    @property
    def gender(self) -> str | None:
        return _GENDER_MAP.get(self.gender_raw.strip())

    @property
    def insurance_type(self) -> str:
        return _INSURANCE_MAP.get(self.insurance_status_raw.strip(), "GKV")


@dataclass
class GDTRecord:
    record_type: str = ""
    treatment_date_raw: str = ""
    patient: GDTPatient = field(default_factory=GDTPatient)
    clinical_texts: list[str] = field(default_factory=list)
    raw_fields: list[tuple[str, str]] = field(default_factory=list)

    @property
    def treatment_date(self) -> str:
        """Konvertiert DDMMYYYY → ISO YYYY-MM-DD, fallback: heute."""
        d = self.treatment_date_raw.strip()
        if len(d) == 8:
            try:
                return f"{d[4:8]}-{d[2:4]}-{d[0:2]}"
            except Exception:
                pass
        return date.today().isoformat()

    @property
    def combined_clinical_text(self) -> str:
        return "\n".join(t for t in self.clinical_texts if t.strip())


# ─── Parser ──────────────────────────────────────────────────────────────────

def parse_gdt_bytes(data: bytes) -> GDTRecord:
    """
    Parst ein GDT 2.1 Binär-Payload (ISO 8859-1) in ein GDTRecord.
    Robust gegen fehlende/ungültige Längenangaben.
    """
    try:
        text = data.decode(GDT_ENCODING)
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")

    fields: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if len(line) < 7:
            continue
        # Erste 3 Zeichen = deklarierte Länge (ignorieren, robust parsen)
        field_id = line[3:7]
        value = line[7:]
        if field_id.isdigit():
            fields.append((field_id, value))

    return _build_record(fields)


def parse_gdt_file(path: str) -> GDTRecord:
    with open(path, "rb") as f:
        return parse_gdt_bytes(f.read())


def _build_record(fields: list[tuple[str, str]]) -> GDTRecord:
    rec = GDTRecord()
    rec.raw_fields = fields

    for fk, val in fields:
        val = val.strip()
        if fk == FK_RECORD_TYPE:
            rec.record_type = val
        elif fk == FK_TREATMENT_DATE:
            rec.treatment_date_raw = val
        elif fk == FK_PATIENT_ID:
            rec.patient.patient_number = val
        elif fk == FK_NAME:
            rec.patient.last_name = val
        elif fk == FK_FIRSTNAME:
            rec.patient.first_name = val
        elif fk == FK_BIRTHDATE:
            rec.patient.birth_date_raw = val
        elif fk == FK_GENDER:
            rec.patient.gender_raw = val
        elif fk == FK_INSURANCE_NO:
            rec.patient.insurance_number = val
        elif fk == FK_INSURANCE_STATUS:
            rec.patient.insurance_status_raw = val
        elif fk == FK_INSURANCE_NAME:
            rec.patient.insurance_name = val
        elif fk == FK_INSURANCE_IK:
            rec.patient.insurance_ik = val
        elif fk in (FK_FREE_TEXT, FK_FINDING_TEXT, FK_DIAG_TEXT):
            if val:
                rec.clinical_texts.append(val)

    return rec


# ─── Exporter ────────────────────────────────────────────────────────────────

def _gdt_line(field_id: str, value: str) -> bytes:
    """Baut eine einzelne GDT-Zeile nach Format LLL+FFFF+content+\\r\\n."""
    encoded = value.encode(GDT_ENCODING, errors="replace")
    total = 3 + 4 + len(encoded) + 2       # LLL + FFFF + content + \r\n
    header = f"{total:03d}{field_id}".encode(GDT_ENCODING)
    return header + encoded + b"\r\n"


def build_gdt_response(
    patient: GDTPatient,
    treatment_date: str,  # ISO YYYY-MM-DD
    validated_gops: list[dict],
    rejected_gops: list[dict],
    sender_id: str = "EBM-Analyzer",
    receiver_id: str = "PVS",
) -> bytes:
    """
    Erstellt eine GDT-Antwortdatei (Satzart 8220) mit validierten EBM-GOPs.
    Kompatibel mit KBV-GDT 2.1 PVS-Import.

    validated_gops: list of {"gop_code": ..., "description": ..., "confidence": ...}
    rejected_gops:  list of {"gop_code": ..., "reason": ...}
    """
    buf = BytesIO()

    # Datum für GDT-Format DDMMYYYY
    try:
        d = datetime.fromisoformat(treatment_date)
        gdt_date = d.strftime("%d%m%Y")
    except Exception:
        gdt_date = datetime.now().strftime("%d%m%Y")

    # Header
    buf.write(_gdt_line(FK_RECORD_TYPE, SATZART_RESPONSE))
    buf.write(_gdt_line(FK_GDT_VERSION, "GDT02.10"))
    buf.write(_gdt_line(FK_CHARSET, "IBM CP850"))
    buf.write(_gdt_line(FK_SENDER_ID, sender_id[:20]))
    buf.write(_gdt_line(FK_RECEIVER_ID, receiver_id[:20]))
    buf.write(_gdt_line(FK_TREATMENT_DATE, gdt_date))

    # Patientenreferenz
    if patient.patient_number:
        buf.write(_gdt_line(FK_PATIENT_ID, patient.patient_number))
    if patient.last_name:
        buf.write(_gdt_line(FK_NAME, patient.last_name))
    if patient.first_name:
        buf.write(_gdt_line(FK_FIRSTNAME, patient.first_name))

    # Validierte GOPs
    if validated_gops:
        buf.write(_gdt_line(FK_FREE_TEXT, "=== EBM-Kodierung (KI-validiert) ==="))
        for gop in validated_gops:
            code = gop.get("gop_code", "")
            desc = gop.get("description") or ""
            conf = gop.get("confidence", 0.0)
            # Beschreibung kürzen auf 60 Zeichen
            desc_short = desc[:60].split(".")[0] if desc else ""
            line = f"GOP {code}  {desc_short}  (Konfidenz: {conf:.0%})"
            buf.write(_gdt_line(FK_FREE_TEXT, line))
    else:
        buf.write(_gdt_line(FK_FREE_TEXT, "Keine abrechnungsfähigen GOPs ermittelt."))

    # Abgelehnte GOPs (kompakt)
    if rejected_gops:
        buf.write(_gdt_line(FK_FREE_TEXT, "--- Abgelehnte GOPs ---"))
        for r in rejected_gops[:10]:
            code = r.get("gop_code") or r.get("code", "")
            reason = r.get("reason") or r.get("rejection_reason") or r.get("validation_stage", "")
            buf.write(_gdt_line(FK_FREE_TEXT, f"GOP {code} abgelehnt: {reason[:40]}"))

    # Abschluss
    buf.write(_gdt_line(FK_FREE_TEXT, f"Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')} | EBM Analyzer"))

    return buf.getvalue()


def gop_results_to_gdt(
    analysis_response: dict,
    patient: GDTPatient | None = None,
) -> bytes:
    """
    Konvertiert ein AnalysisResponse-Dict (aus der API) in GDT-Bytes.
    Einstiegspunkt für API-Handler.
    """
    gops = analysis_response.get("gop_results", [])
    rejected = analysis_response.get("rejected_gops", [])
    treatment_date = analysis_response.get("treatment_date", date.today().isoformat())

    validated = [
        {
            "gop_code": g.get("gop_code"),
            "description": g.get("description"),
            "confidence": g.get("confidence", 0.0),
        }
        for g in gops
    ]
    return build_gdt_response(
        patient=patient or GDTPatient(),
        treatment_date=treatment_date,
        validated_gops=validated,
        rejected_gops=rejected,
    )
