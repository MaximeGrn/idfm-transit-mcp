import httpx
import pytest

from idfm_transit_mcp.client import PrimClient
from idfm_transit_mcp.config import Settings
from idfm_transit_mcp.tools import TransitTools


@pytest.fixture
async def make_tools():
    clients = []

    def make(handler, **settings):
        client = PrimClient(
            Settings(api_key="test-secret", retries=0, **settings), httpx.MockTransport(handler)
        )
        clients.append(client)
        return TransitTools(client)

    yield make
    for client in clients:
        await client.close()


def page(collection, items, total=None, disruptions=None):
    return {
        collection: items,
        "disruptions": disruptions or [],
        "pagination": {
            "total_result": len(items) if total is None else total,
            "items_on_page": len(items),
            "items_per_page": 100,
        },
    }
