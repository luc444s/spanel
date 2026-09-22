from __future__ import annotations

from sqlalchemy.orm import Session

from systutor.api.v1.core.services.users import register_user_category


def _auth_headers(client) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "ChangeMe123!"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_user_categories_are_host_registered(client, db_session: Session, seeded_demo) -> None:
    register_user_category("operator", "Operator", ["driver"])

    response = client.get("/api/v1/core/users/categories", headers=_auth_headers(client))
    assert response.status_code == 200
    categories = {item["value"]: item["label"] for item in response.json()}
    assert categories["operator"] == "Operator"


def test_unknown_category_is_ignored_without_error(
    client, db_session: Session, seeded_demo
) -> None:
    response = client.post(
        "/api/v1/core/users",
        headers=_auth_headers(client),
        json={
            "name": "No Category User",
            "email": "no-category@example.com",
            "password": "NoCategory123!",
            "branch_id": seeded_demo["branch_id"],
            "category": "does-not-exist",
            "role_ids": [],
            "warehouse_ids": [],
        },
    )
    assert response.status_code == 201
    assert response.json()["category"] == "does-not-exist"
