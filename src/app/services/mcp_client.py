"""
MCP client wrapper for the FastAPI orchestrator.

Connects to the MCP server container via SSE and calls the deterministic
validation tools. No LLM access happens here.
"""
import json
import logging
from typing import Any

import httpx
from mcp.client.sse import sse_client
from mcp import ClientSession

from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class MCPClient:
    """Thread-safe MCP client with connection-per-call semantics."""

    def __init__(self):
        self.server_url = settings.mcp_server_url
        self.secret = settings.mcp_secret
        self._headers = {"X-MCP-Secret": self.secret}

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call an MCP tool and return the parsed JSON result."""
        sse_url = f"{self.server_url}/sse"
        try:
            async with sse_client(url=sse_url, headers=self._headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)

            # MCP returns TextContent; parse the embedded JSON
            if result.content:
                text = result.content[0].text
                return json.loads(text)
            return {}

        except Exception as e:
            logger.error("MCP tool call '%s' failed: %s", tool_name, e)
            raise RuntimeError(f"MCP-Validierung fehlgeschlagen: {e}") from e

    async def validate_full(
        self,
        gop_codes: list[str],
        treatment_date: str,
        patient_id: str | None = None,
        session_id: str | None = None,
        patient_override: dict | None = None,
    ) -> dict:
        """
        Run all three MCP validations sequentially.

        Returns a consolidated result:
          - final_allowed: GOPs that passed all three checks
          - rejected: every rejected GOP with reason and validation stage
          - excl_result / time_result / demo_result: per-stage details
        """
        base_args = {"gop_codes": gop_codes, "treatment_date": treatment_date}
        if patient_id:
            base_args["patient_id"] = patient_id
        if session_id:
            base_args["session_id"] = session_id

        # 1. Mutual exclusion check
        excl_result = await self.call_tool("ebm_validator_exclusions", base_args)
        codes_after_excl = excl_result.get("allowed", gop_codes)

        # 2. Time budget check (only on codes that survived stage 1)
        time_args = {**base_args, "gop_codes": codes_after_excl}
        time_result = await self.call_tool("ebm_validator_time_budget", time_args)
        flagged_time = {f["code"] for f in time_result.get("flagged_codes", [])}
        codes_after_time = [c for c in codes_after_excl if c not in flagged_time]

        # 3. Demographic check
        demo_args: dict[str, Any] = {**base_args, "gop_codes": codes_after_time}
        if patient_override:
            demo_args["patient_override"] = patient_override
        demo_result = await self.call_tool("ebm_validator_demographics", demo_args)
        final_allowed = demo_result.get("allowed", codes_after_time)

        # Consolidate all rejections with their originating stage
        rejected: list[dict] = []
        for b in excl_result.get("banned", []):
            rejected.append({**b, "validation_stage": "exclusion"})
        for f in time_result.get("flagged_codes", []):
            rejected.append({**f, "validation_stage": "time_budget"})
        for v in demo_result.get("violations", []):
            rejected.append({**v, "validation_stage": "demographics"})

        return {
            "final_allowed": final_allowed,
            "rejected": rejected,
            "excl_result": excl_result,
            "time_result": time_result,
            "demo_result": demo_result,
        }

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.server_url}/health")
                return resp.status_code == 200
        except Exception:
            return False
