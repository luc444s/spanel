def test_legacy_health_alias(client) -> None:
    response = client.get("/api/v1/system/health/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "api"


def test_legacy_ready_alias(client, seeded_demo: dict[str, str]) -> None:
    response = client.get("/api/v1/system/health/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database_connected"] is True
