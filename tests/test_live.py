"""Explicit opt-in: these checks consume quota, and never print credentials."""

import os

import pytest
from mcp.client import Client

from idfm_transit_mcp.client import NAVITIA, PrimClient
from idfm_transit_mcp.config import Settings
from idfm_transit_mcp.server import create_server
from idfm_transit_mcp.tools import TransitTools

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("IDFM_RUN_LIVE") != "1", reason="Set IDFM_RUN_LIVE=1 to use PRIM"),
]


async def test_official_api_end_to_end():
    prim = PrimClient(Settings.from_env())
    tools = TransitTools(prim)
    try:
        results = {}
        for query in ("République", "Télégraphe", "Gare de l'Est"):
            response = await tools.search_places(query)
            assert response.status == "ok"
            places = response.data["places"]
            results[query] = next(
                p
                for p in places
                if p["type"] == "stop_area" and p.get("label", "").endswith("(Paris)")
            )
            raw = await prim.get(NAVITIA + "/places", {"q": query, "count": 10}, 3600)
            assert results[query]["id"] in {p["id"] for p in raw["places"]}
        for line in ("Métro 5", "Métro 11", "RER A", "T3b"):
            status = await tools.get_line_status(line)
            assert status.status in ("ok", "empty") and status.data["complete"]
            assert status.data["line"]["id"].startswith("line:")
        journey = await tools.plan_journey(
            results["Télégraphe"]["id"], results["Gare de l'Est"]["id"]
        )
        assert journey.data["journeys"]
        assert journey.data["journeys"][0]["duration_seconds"] > 0
        assessment = await tools.check_journey_status(journey.data["journeys"][0])
        assert assessment.data["assessment"] in (
            "disruption_detected",
            "no_known_disruption",
            "information_unavailable",
        )
        stop = await tools.get_stop_info(results["République"]["id"])
        assert stop.data["lines"] and stop.data["stop"]["siri_monitoring_refs"]
        departures = await tools.get_next_departures(
            results["République"]["id"], timetable="realtime"
        )
        assert departures.status in ("ok", "empty")
        assert "departures" in departures.data
    finally:
        await prim.close()


async def test_real_mcp_client_calls_prim():
    async with Client(create_server()) as client:
        result = await client.call_tool("get_line_status", {"line": "Métro 11"})
        assert result.structured_content["data"]["line"]["code"] == "11"
