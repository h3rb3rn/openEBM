# openEBM — Technical Documentation

openEBM is a sovereign, GDPR-compliant, air-gap-ready EBM billing assistant for German medical practices. It combines probabilistic LLM-based text extraction with deterministic MCP rule validation and exposes interoperability interfaces for legacy PVS software (GDT 2.1) and modern EMR systems (HL7 FHIR R4).

!!! success "Verified LLM backend"
    The full analysis pipeline (RAG → LLM → MCP validation) has been successfully tested end-to-end against a **locally hosted `qwen3.6:35b`** model — no external cloud LLM API involved. See [Screenshots](screenshots.md) for a real analysis result produced by this model.

## Quick navigation

| Topic | Description |
|---|---|
| [Screenshots](screenshots.md) | The application in action |
| [Architecture overview](architecture/overview.md) | Stack, components, split-brain design |
| [Data flow](architecture/data-flow.md) | RAG → LLM → MCP pipeline |
| [Database schema](architecture/database.md) | PostgreSQL tables and relationships |
| [API reference](api/authentication.md) | REST endpoints, auth, schemas |
| [GDT interface](interop/gdt.md) | Legacy PVS integration via GDT 2.1 |
| [FHIR R4 interface](interop/fhir.md) | Modern EMR integration |
| [MCP external tool](interop/mcp-tool.md) | AI agent integration |
| [Deployment](deployment/docker-compose.md) | Docker Compose setup |
| [Environment variables](deployment/environment.md) | Full `.env` reference |
| [Changelog](development/changelog.md) | Version history |

## Technology stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI (Python 3.12) |
| Database | PostgreSQL 16 via SQLAlchemy 2 (asyncpg) |
| Vector store | ChromaDB (semantic GOP retrieval) |
| Graph database | Neo4j 5 (GOP exclusion rules, demographic restrictions) |
| Cache / sessions | Valkey (Redis-compatible) |
| LLM provider | Configurable: local Ollama **or** any OpenAI-compatible API |
| Validation server | MCP server (Starlette/SSE) |
| Frontend | Server-rendered Jinja2 templates, vanilla JS, client-side i18n |
| Containerisation | Docker Compose, multi-service |
