# Development Setup

## Prerequisites

- Docker and Docker Compose (v2)
- Python 3.12 (for local linting / IDE support)

## First run

```bash
cd /opt/deployment/EBM

# Copy and edit environment configuration
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY, INTERNAL_API_KEY, MCP_SECRET

# Build all images
docker compose build

# Start all services
docker compose up -d

# Check health
curl http://localhost:4244/health
```

The app seeds a demo tenant on first startup:

- **Email**: `admin@demo.local`
- **Password**: `demo1234`

## After code changes

```bash
# Rebuild and restart only the changed service
docker compose build app && docker compose up -d app
docker compose build mcp_server && docker compose up -d mcp_server
```

## Viewing logs

```bash
docker compose logs -f app
docker compose logs -f mcp_server
```

## Running the EBM catalog import

```bash
# Via admin UI: Administration → Import → Import starten
# Or via API:
curl -X POST http://localhost:4244/api/admin/import/trigger \
  -H "Authorization: Bearer <token>"
```

## MkDocs documentation

```bash
# Install MkDocs with Material theme
pip install mkdocs-material

# Serve docs locally
cd /opt/deployment/EBM
mkdocs serve
# Open http://127.0.0.1:8000

# Build static site
mkdocs build
```

## Directory structure

```
EBM/
├── docker/                  # Dockerfiles
│   ├── app.Dockerfile
│   └── mcp.Dockerfile
├── docker-compose.yml
├── docs/                    # MkDocs documentation (this site)
├── mkdocs.yml               # MkDocs configuration
├── CHANGELOG.md             # Version history
└── src/
    ├── app/                 # FastAPI application
    │   ├── api/             # Route handlers
    │   ├── models/          # SQLAlchemy models
    │   ├── services/        # Business logic
    │   ├── static/          # Static files (JS, CSS, i18n catalogs)
    │   └── templates/       # Jinja2 HTML templates
    ├── integration/         # GDT/BDT file format handlers
    ├── ingestion/           # EBM catalog import pipeline
    └── mcp_server/          # MCP validation server
        └── tools/           # Individual validation tools
```
