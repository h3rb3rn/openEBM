"""
EBM MCP Validation Server

Exposes deterministic validation tools over MCP/SSE:
  1. ebm_validator_exclusions   — mutual exclusion checks (§ 1 Abs. 3 EBM)
  2. ebm_validator_time_budget  — time profile plausibility (§ 46 BMV-Ä)
  3. ebm_validator_demographics — age, gender and insurance restrictions

Transport: HTTP/SSE (Starlette + uvicorn)
Auth:      shared secret via X-MCP-Secret header
"""
import logging
import sys
from contextlib import asynccontextmanager

import uvicorn

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .config import get_mcp_settings
from .tools.exclusions import check_exclusions
from .tools.time_budget import check_time_budget
from .tools.demographics import check_demographics
from .neo4j_client import close_driver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
settings = get_mcp_settings()


# ─────────────────────────── MCP Server ─────────────────────────────

mcp_server = Server("ebm-validator-mcp")

TOOLS: list[Tool] = [
    Tool(
        name="analyze_clinical_text_for_ebm",
        description=(
            "Full EBM billing analysis for a clinical text. "
            "Uses GraphRAG (ChromaDB + Neo4j), an LLM, and deterministic MCP validation. "
            "Returns validated EBM GOPs with confidence scores, text anchors and MCP audit report. "
            "Designed for external AI agents and clinic sub-systems as an air-gapped billing oracle."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "clinical_text": {
                    "type": "string",
                    "description": "Free-text clinical document (physician letter, finding, epicrisis) in German.",
                },
                "insurance_type": {
                    "type": "string",
                    "enum": ["GKV", "PKV", "SELBSTZAHLER", "BG"],
                    "description": "Patient insurance class (default: GKV)",
                },
                "treatment_date": {
                    "type": "string",
                    "description": "Treatment date ISO-8601 (YYYY-MM-DD, default: today)",
                },
                "patient_dob": {
                    "type": "string",
                    "description": "Date of birth ISO-8601 YYYY-MM-DD (optional, used for age restrictions)",
                },
                "patient_gender": {
                    "type": "string",
                    "enum": ["m", "w", "d"],
                    "description": "Gender (optional, used for gender-specific GOPs)",
                },
                "use_rag": {
                    "type": "boolean",
                    "description": "Enable knowledge-base retrieval (default: true)",
                },
                "reasoning": {
                    "type": "boolean",
                    "description": "Enable chain-of-thought reasoning — slower (default: false)",
                },
            },
            "required": ["clinical_text"],
        },
    ),
    Tool(
        name="ebm_validator_exclusions",
        description=(
            "Check mutual exclusions (§ 1 Abs. 3 EBM) between proposed GOPs "
            "for a treatment quarter. Returns an allowed/banned matrix."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "gop_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of GOP codes (e.g. ['01435', '01600'])",
                },
                "treatment_date": {
                    "type": "string",
                    "description": "Treatment date ISO-8601 (YYYY-MM-DD)",
                },
                "patient_id": {"type": "string", "description": "Patient UUID (optional)"},
                "session_id": {"type": "string", "description": "Transient session ID (optional)"},
            },
            "required": ["gop_codes", "treatment_date"],
        },
    ),
    Tool(
        name="ebm_validator_time_budget",
        description=(
            "Time profile plausibility check per KBV guidelines. "
            "Verifies that the sum of GOP time values does not exceed the daily maximum (780 min)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "gop_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "treatment_date": {"type": "string"},
                "patient_id": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["gop_codes", "treatment_date"],
        },
    ),
    Tool(
        name="ebm_validator_demographics",
        description=(
            "Check demographic restrictions (age, gender, insurance class) "
            "of GOPs against patient data."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "gop_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "treatment_date": {"type": "string"},
                "patient_id": {
                    "type": "string",
                    "description": "Patient UUID (persistent mode)",
                },
                "patient_override": {
                    "type": "object",
                    "description": "Inline patient data (instant mode — no DB access)",
                    "properties": {
                        "date_of_birth": {"type": "string"},
                        "gender": {"type": "string", "enum": ["m", "w", "d"]},
                        "insurance_type": {"type": "string", "enum": ["GKV", "PKV", "SELBSTZAHLER", "BG"]},
                    },
                },
            },
            "required": ["gop_codes", "treatment_date"],
        },
    ),
]


async def _call_analyze_clinical_text(args: dict) -> dict:
    """
    Call the FastAPI interop endpoint /api/interop/internal/analyze.
    Container-internal communication via X-Internal-Key.
    """
    import httpx
    from datetime import date as _date

    payload = {
        "clinical_text": args.get("clinical_text", ""),
        "insurance_type": args.get("insurance_type", "GKV"),
        "treatment_date": args.get("treatment_date", _date.today().isoformat()),
        "patient_dob": args.get("patient_dob"),
        "patient_gender": args.get("patient_gender"),
        "use_rag": args.get("use_rag", True),
        "reasoning": args.get("reasoning", False),
    }

    url = f"{settings.app_internal_url}/api/interop/internal/analyze"
    headers = {"X-Internal-Key": settings.internal_api_key, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"Analysis call failed: HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": f"Analysis call failed: {e}"}


@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    import json

    try:
        if name == "analyze_clinical_text_for_ebm":
            result = await _call_analyze_clinical_text(arguments)
        elif name == "ebm_validator_exclusions":
            result = await check_exclusions(
                gop_codes=arguments["gop_codes"],
                treatment_date_str=arguments["treatment_date"],
                patient_id=arguments.get("patient_id"),
                session_id=arguments.get("session_id"),
            )
        elif name == "ebm_validator_time_budget":
            result = await check_time_budget(
                gop_codes=arguments["gop_codes"],
                treatment_date_str=arguments["treatment_date"],
                patient_id=arguments.get("patient_id"),
                session_id=arguments.get("session_id"),
            )
        elif name == "ebm_validator_demographics":
            result = await check_demographics(
                gop_codes=arguments["gop_codes"],
                treatment_date_str=arguments["treatment_date"],
                patient_id=arguments.get("patient_id"),
                patient_override=arguments.get("patient_override"),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.exception("Error in tool %s: %s", name, e)
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]


# ─────────────────────────── Auth Middleware ─────────────────────────

class MCPAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        secret = request.headers.get("X-MCP-Secret")
        if secret != settings.mcp_secret:
            return Response("Unauthorized", status_code=401)
        return await call_next(request)


# ─────────────────────────── Starlette App ───────────────────────────

sse_transport = SseServerTransport("/messages/")


async def handle_sse(request: Request) -> Response:
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0],
            streams[1],
            mcp_server.create_initialization_options(),
        )
    return Response()


async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse(
        {"status": "ok", "service": "ebm-mcp", "tools": [t.name for t in TOOLS]}
    )


@asynccontextmanager
async def lifespan(app: Starlette):
    logger.info("EBM MCP Server started (port %s)", settings.mcp_port)
    yield
    await close_driver()
    logger.info("EBM MCP Server stopped")


app = Starlette(
    routes=[
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ],
    middleware=[Middleware(MCPAuthMiddleware)],
    lifespan=lifespan,
)


if __name__ == "__main__":
    uvicorn.run(
        "src.mcp_server.main:app",
        host="0.0.0.0",
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
