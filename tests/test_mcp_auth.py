from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from birzha.security import McpBearerAuthMiddleware


async def ok(_request):
    return JSONResponse({"ok": True})


def _client() -> TestClient:
    app = Starlette(routes=[Route("/healthz", ok), Route("/mcp", ok, methods=["GET", "POST"])])
    app.add_middleware(McpBearerAuthMiddleware, token="secret-token")
    return TestClient(app)


def test_health_remains_public() -> None:
    with _client() as client:
        assert client.get("/healthz").status_code == 200


def test_mcp_rejects_missing_or_wrong_token() -> None:
    with _client() as client:
        assert client.get("/mcp").status_code == 401
        assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_mcp_accepts_correct_token() -> None:
    with _client() as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer secret-token"})
        assert response.status_code == 200
        assert response.json() == {"ok": True}
