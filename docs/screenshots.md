# Screenshots

Alle Aufnahmen stammen aus einer laufenden Instanz mit Testdaten (Platzhalterpatient „Mustermann"). Die Analyse-Ergebnisse sind echt — keine gestellten Mockups. Klick auf ein Bild öffnet es in Originalgröße.

## Analyse-Ergebnis

Ergebnis einer echten Instant-Analyse mit einem lokal gehosteten `qwen3.6:35b`: links der Bericht mit farblich hervorgehobenem Textbeleg, rechts die GOP-Legende und die MCP-Validierungs-Kennzahlen (validiert/abgelehnt, Quartal, Session). Die Hervorhebungsfarben im Bericht entsprechen exakt den Farben in der Legende.

[![Annotierter Bericht mit hervorgehobenen GOP-Ziffern, GOP-Legende und MCP-Kennzahlen](assets/screenshots/analyse-ergebnis.jpg)](assets/screenshots/analyse-ergebnis.jpg){target=_blank}

## Dashboard

Übersicht über Instant- und Persistent-Modus, offene Fallakten und Kennzahlen des aktuellen Quartals.

[![Dashboard mit Fallakten-Übersicht](assets/screenshots/dashboard.jpg)](assets/screenshots/dashboard.jpg){target=_blank}

## Fallakten

Fallakten-Liste mit Status, GOP-Anzahl und Quartalszuordnung.

[![Fallakten-Übersicht](assets/screenshots/fallakten.jpg)](assets/screenshots/fallakten.jpg){target=_blank}

## Fallakte im Detail

Fallakte `819cfd50-514e-4859-87b4-13a223880062` (2026-07-06, 2026Q3) mit eingeblendetem Arztbericht (Textbeleg farblich markiert) und den vier noch offenen GOP-Vorschlägen samt Konfidenz, Textbeleg und Human-in-the-Loop-Entscheidung (Akzeptieren/Ablehnen). Darunter das Audit-Log.

[![Fallakte im Detail mit eingeblendetem Arztbericht und offenen GOP-Vorschlägen](assets/screenshots/fallakte-detail.jpg)](assets/screenshots/fallakte-detail.jpg){target=_blank}

## Administration

### System & Monitoring

Live-Status aller Backend-Dienste (PostgreSQL, Valkey, ChromaDB, MCP-Server, Neo4j), die aktive LLM-Anbindung und die deterministischen MCP-Validierungswerkzeuge.

[![System-Status und Monitoring im Admin-Bereich](assets/screenshots/admin-system.jpg)](assets/screenshots/admin-system.jpg){target=_blank}

### Datenquellen

Übersicht aller Datenquellen des Systems: EBM-Katalog, Patientenakten, GOP-Ausschlussregeln, semantische Embeddings, Audit-Log und Instant-Sessions — jeweils mit Speicherort und Aktualisierungsrhythmus.

[![Datenquellen- und Datenbankstatus im Admin-Bereich](assets/screenshots/admin-datenquellen.jpg)](assets/screenshots/admin-datenquellen.jpg){target=_blank}

### Import

EBM-Katalog-Import: Katalogdatei-Metadaten, aktueller Datenbankstatus (GOPs in ChromaDB/Neo4j) und die bekannten Importquellen (KBV-Primärquelle, Seed-Daten-Fallback).

[![EBM-Katalog-Import im Admin-Bereich](assets/screenshots/admin-import.jpg)](assets/screenshots/admin-import.jpg){target=_blank}

### Interoperabilität

Status aller Interop-Schnittstellen: GDT/BDT-Bridge, HL7 FHIR R4, MCP External Tool und interner Kanal.

[![Interoperabilitäts-Übersicht im Admin-Bereich](assets/screenshots/admin-interop.jpg)](assets/screenshots/admin-interop.jpg){target=_blank}

## Profil: Passwort & 2FA

Selbstverwaltung: Passwort ändern und Zwei-Faktor-Authentifizierung (TOTP) aktivieren.

[![Profilseite mit Passwort-Änderung und 2FA](assets/screenshots/profil.jpg)](assets/screenshots/profil.jpg){target=_blank}

## Login

Anmeldeseite — Session läuft über ein httpOnly-Cookie, serverseitig geprüft vor jedem Seitenaufruf.

[![Login-Seite](assets/screenshots/login.jpg)](assets/screenshots/login.jpg){target=_blank}
