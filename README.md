# openEBM

**Souveräne, quelloffene EBM-Kodierungsunterstützung für die deutsche Arztpraxis.**

openEBM liest medizinische Arztberichte, schlägt EBM-Ziffern (GOP) mit exaktem Textbeleg vor und lässt jede einzelne Ziffer von einem deterministischen, regelbasierten Validator prüfen, bevor sie abgerechnet wird. Das System läuft vollständig auf eigener Infrastruktur – air-gap-fähig, ohne Pflicht zu einer Cloud-Anbindung und ohne dass Patiendaten die Praxis verlassen müssen.

Split-Brain-Architektur: ein Sprachmodell (LLM) macht probabilistische Vorschläge, ein separater, in Neo4j abgebildeter Regelgraph validiert diese Vorschläge deterministisch (Ausschlüsse, Zeitbudget, Demografie). Die letzte Entscheidung trifft immer ein Mensch (Human-in-the-Loop), lückenlos protokolliert im Audit-Log.

> **Getestet mit lokalem LLM:** Die komplette Analyse-Pipeline (RAG → LLM → MCP-Validierung) wurde erfolgreich End-to-End gegen ein **lokal gehostetes `qwen3.6:35b`-Modell** getestet — ganz ohne externe Cloud-LLM-API. Siehe [Screenshots](#screenshots) für ein echtes Analyseergebnis.

---

## Inhalt

- [Screenshots](#screenshots)
- [Voraussetzungen](#voraussetzungen)
- [Schnellstart](#schnellstart)
- [Betriebsmodi](#betriebsmodi)
- [Architektur](#architektur--stack)
- [Verzeichnisstruktur](#verzeichnisstruktur)
- [Interoperabilität](#interoperabilität)
- [Konfiguration & Sicherheit](#konfiguration--sicherheit)
- [Dokumentation](#dokumentation)
- [Entwicklung](#entwicklung)
- [Lizenz](#lizenz)

---

## Screenshots

| Analyse-Ergebnis (echtes Modell-Ergebnis, `qwen3.6:35b`) | Dashboard |
|---|---|
| ![Annotierter Bericht mit hervorgehobenen GOP-Ziffern](docs/assets/screenshots/analyse-ergebnis.jpg) | ![Dashboard](docs/assets/screenshots/dashboard.jpg) |

Weitere Ansichten (Fallakten, Interoperabilität, Profil/2FA, Login): siehe [`docs/screenshots.md`](docs/screenshots.md).

---

## Voraussetzungen

- Docker und Docker Compose (v2)
- Für lokale Entwicklung am Frontend: Node.js 20+ (nur für den Tailwind-CSS-Build, nicht zur Laufzeit erforderlich)
- Optional: eine laufende Ollama-Instanz für ein vollständig lokales, air-gapped LLM-Backend

## Schnellstart

```bash
git clone https://github.com/h3rb3rn/openEBM.git
cd openEBM

# Umgebungsvariablen konfigurieren
cp .env.example .env
# .env öffnen und ALLE "CHANGE_ME_*"-Platzhalter durch echte, zufällige
# Werte ersetzen (Passwörter, Secrets, Tokens). Siehe Abschnitt
# "Konfiguration & Sicherheit" weiter unten.

# Stack bauen und starten
docker compose up -d --build

# EBM-Katalog initial laden (Neo4j + ChromaDB befüllen)
docker compose --profile ingestion run ingestion
```

Die Anwendung ist danach unter `http://<APP_HOST>:<APP_PORT>` erreichbar (Standard: `http://127.0.0.1:8080`).

Beim allerersten Start wird einmalig ein Demo-Mandant mit Administrator-Zugang angelegt (`admin@demo.local` / `demo1234`). **Diesen Zugang nach dem ersten Login umgehend deaktivieren oder das Passwort ändern.**

## Betriebsmodi

| Modus | Speicherung | Audit-Log | Einsatzzweck |
|---|---|---|---|
| **Instant** (transient) | Nur Valkey, TTL-begrenzt | Nein | Schnelle Ad-hoc-Analyse ohne Datenpersistenz |
| **Persistent** | PostgreSQL (Fallakte, GOP-Vorschläge) | Ja | Vollständige Falldokumentation mit Nachvollziehbarkeit |

## Architektur & Stack

| Komponente | Technologie | Rolle |
|---|---|---|
| API & Frontend | FastAPI + Jinja2 (Python 3.12) | REST-API, serverseitig gerenderte Weboberfläche |
| Datenbank | PostgreSQL 16 / SQLAlchemy 2 | Mandanten, Nutzer, Fallakten, Audit-Log |
| Vektorsuche | ChromaDB | Semantisches Retrieval im EBM-Katalog |
| Graphdatenbank | Neo4j 5 | Ausschlussregeln, Zeitbudget- & Demografie-Restriktionen |
| Cache | Valkey (Redis-kompatibel) | Instant-Sessions, GDT-Export-Cache |
| LLM | frei wählbar | Lokales Ollama-Modell oder OpenAI-kompatible API — getestet mit lokalem `qwen3.6:35b` |
| Validator | MCP-Server (Starlette/SSE) | Deterministische, LLM-unabhängige Regelprüfung |
| Betrieb | Docker Compose | Multi-Service-Deployment, air-gap-fähig |

Ausführliche Architekturbeschreibung inkl. Datenflüssen, Datenbankschema und Sicherheitsmodell: siehe [`docs/architecture/`](docs/architecture/overview.md).

## Verzeichnisstruktur

```
openEBM/
├── docker/                  # Dockerfiles (app, mcp-server, ingestion)
├── docker-compose.yml       # Gesamter Service-Stack
├── docs/                    # MkDocs-Dokumentation (technische Tiefe)
├── mkdocs.yml
├── CHANGELOG.md
├── .env.example             # Vorlage für Umgebungsvariablen
└── src/
    ├── app/                 # FastAPI-Anwendung (API, Templates, Static Assets)
    ├── integration/         # GDT/BDT-Dateiformat-Handler
    ├── ingestion/           # EBM-Katalog-Import-Pipeline
    └── mcp_server/          # Deterministischer MCP-Validierungsserver
```

## Interoperabilität

openEBM ist als Sidecar-Dienst für bestehende Systeme konzipiert:

- **GDT 2.1** – Dateibasierter Austausch mit gängiger Praxisverwaltungssoftware (PVS)
- **HL7 FHIR R4** – Custom Operation `$analyze-ebm` für moderne Klinikinformationssysteme
- **MCP External Tool** – `analyze_clinical_text_for_ebm` als Werkzeug für externe KI-Agenten im Kliniknetz

Details: [`docs/interop/`](docs/interop/gdt.md)

## Konfiguration & Sicherheit

- **`.env` wird nicht versioniert** (siehe `.gitignore`) – sie enthält Klartext-Passwörter und Secrets für Datenbank, Cache, JWT-Signierung und den internen MCP-Kanal. Nur `.env.example` als Vorlage ist Teil des Repositories.
- Vor dem produktiven Betrieb müssen **alle** `CHANGE_ME_*`-Platzhalter in `.env` ersetzt werden: `POSTGRES_PASSWORD`, `VALKEY_PASSWORD`, `CHROMA_TOKEN`, `NEO4J_PASSWORD`, `MCP_SECRET`, `SECRET_KEY`, `INTERNAL_API_KEY`.
- `COOKIE_SECURE=true` erst setzen, sobald TLS vor der Anwendung terminiert wird (Reverse Proxy) – andernfalls verwirft der Browser das Session-Cookie.
- Die Web-Oberfläche authentifiziert per httpOnly-Session-Cookie; geschützte Seiten werden serverseitig geprüft, bevor sie ausgeliefert werden.
- Vollständige Produktions-Checkliste: [`docs/deployment/production.md`](docs/deployment/production.md)

## Dokumentation

Die vollständige technische Dokumentation liegt als MkDocs-Projekt unter [`docs/`](docs/index.md) und wird bei jeder Änderung auf `main` automatisch nach GitHub Pages veröffentlicht (siehe [`.github/workflows/docs.yml`](.github/workflows/docs.yml)):

**Veröffentlichte Dokumentation:** https://h3rb3rn.github.io/openEBM/

Lokal bauen und ansehen:

```bash
pip install -r docs/requirements.txt
mkdocs serve
# http://127.0.0.1:8000
```

## Entwicklung

Nach Änderungen an Templates oder JavaScript muss das Frontend neu gebaut werden (Tailwind-CSS wird lokal kompiliert, kein CDN):

```bash
npm install
npm run build:css
```

Nach Backend-Änderungen genügt ein Rebuild des betroffenen Containers:

```bash
docker compose build app && docker compose up -d app
```

Verbindliche Coding-Konventionen (Sprache, i18n, Changelog-Pflicht): siehe [`docs/development/conventions.md`](docs/development/conventions.md).

## Lizenz

Dieses Projekt wird quelloffen entwickelt. Die endgültige Lizenz wird in einer `LICENSE`-Datei im Repository-Root veröffentlicht.
