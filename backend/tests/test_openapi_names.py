"""E53: the description of nextime's interface keeps the names its own ways had before the settings pass (stand
``konten``): what the modules' contract tests do not hold. ``app.openapi()`` (published only with
``NEXTIME_API_DOCS``)."""

from __future__ import annotations

from typing import Any

from app.main import app


def _operation(path: str, method: str) -> dict[str, Any]:
    return app.openapi()["paths"][path][method]


def test_disconnecting_all_keeps_its_summary_of_before() -> None:
    found = _operation("/api/auth/access/disconnect", "post")
    assert found["summary"] == "Disconnect all: every device, API token and passkey of the account"
    assert found["operationId"] == "disconnect_all_api_auth_access_disconnect_post"


def test_restoring_an_event_from_the_trash_keeps_its_schema_restore_in() -> None:
    body = _operation("/api/trash/events/{event_id}/restore", "post")["requestBody"]["content"]["application/json"]
    assert body["schema"] == {"anyOf": [{"$ref": "#/components/schemas/RestoreIn"}, {"type": "null"}],
                              "title": "Payload"}
    assert set(app.openapi()["components"]["schemas"]["RestoreIn"]["properties"]) == {"calendar_id"}


def test_the_backups_of_before_keep_their_names() -> None:
    restore = _operation("/api/backups/{name}/restore", "post")
    assert (restore["operationId"], restore["summary"]) == ("restore_api_backups__name__restore_post", "Restore")
    schemas = app.openapi()["components"]["schemas"]
    assert {"CreateIn", "DownloadIn", "BackupScheduleIn", "app__routers__backups__PasswordIn"} <= set(schemas)
