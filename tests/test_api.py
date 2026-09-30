"""Unit tests for FastAPI endpoints."""

from fastapi.testclient import TestClient
from config.settings import settings
from src.api.main import app

# Ensure tests use mock collector for instant execution
settings.COLLECTOR_MODE = "mock"

client = TestClient(app)


def test_root_endpoint():
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "Personal AI Content Agent" in data["service"]


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_accounts_endpoint():
    resp = client.get("/api/v1/accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] > 0
    assert len(data["accounts"]) == data["count"]


def test_research_run_endpoint():
    resp = client.post("/api/v1/research/run?send_telegram=false")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["result"] is not None
    assert len(data["result"]["items"]) > 0


def test_get_latest_research():
    resp = client.get("/api/v1/research/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["top_pick"] is not None
