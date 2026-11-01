import sys

import httpx
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from idfm_transit_mcp.client import PrimClient
from idfm_transit_mcp.config import Settings
from idfm_transit_mcp.server import create_server

EXPECTED = {
    "search_places",
    "plan_journey",
    "get_line_status",
    "get_network_disruptions",
    "get_next_departures",
    "get_stop_info",
    "check_journey_status",
}


async def test_real_stdio_client_discovers_tools_and_handles_missing_key():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "idfm_transit_mcp.server"],
        env={"IDFM_API_KEY": "", "IDFM_API_KEY_FILE": ""},
    )
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == EXPECTED
        assert all(tool.annotations.read_only_hint for tool in tools)
        result = await client.call_tool("search_places", {"query": "République"})
        assert result.structured_content["error"]["code"] == "missing_api_key"
        assert not result.is_error


async def test_mcp_structured_output_and_cached_retrieval_time():
    def handler(req):
        return httpx.Response(200, json={"places": []})

    prim = PrimClient(Settings(api_key="test"), httpx.MockTransport(handler))
    async with Client(create_server(client=prim)) as client:
        first = await client.call_tool("search_places", {"query": "missing"})
        second = await client.call_tool("search_places", {"query": "missing"})
        assert first.structured_content["status"] == "empty"
        assert first.structured_content["retrieved_at"] == second.structured_content["retrieved_at"]
        invalid = await client.call_tool("get_stop_info", {"stop_id": "../../../private"})
        assert invalid.structured_content["error"]["code"] == "invalid_input"
