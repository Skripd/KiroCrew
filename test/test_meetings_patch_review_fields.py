"""A task field edit (PATCH /tasks) leaves the review state and the filing record alone.

Only the review and file routes write ``review_status`` and ``filed_ref``. A PATCH that
carried them would reset a filed task to pending, and the next File would create a
second item in the tracker. The edit still saves its other fields and still answers
200, naming the keys it left alone in ``ignored_fields``. The shared fixtures are in
``meetings_helpers``.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from aiohttp import web
from meetings_helpers import (  # noqa: F401
    app_fixture,
    client_for,
    enabled_fixture,
    fake_sessions_fixture,
    reset_module_state_fixture,
    root_fixture,
)
from meetings_routes_helpers import BASE

from kiro_crew.apps.builtins.meetings.backend import constants as k
from kiro_crew.apps.builtins.meetings.backend.providers import tasks as taskprov


@pytest.mark.asyncio
async def test_a_field_edit_cannot_unfile_a_task(app: web.Application, root: Path) -> None:
    created: list[str] = []
    real_create = taskprov.LocalTaskProvider.create

    def _counting_create(self, draft):  # type: ignore[no-untyped-def]
        created.append(draft.description)
        return real_create(self, draft)

    async with client_for(app) as client:
        await client.post(f"{BASE}/meetings/edit/init", json={"title": "Edit"})
        await client.post(f"{BASE}/meetings/edit/tasks", json={"description": "file me once"})
        listed = (await (await client.get(f"{BASE}/meetings/edit/tasks")).json())["tasks"]
        task_id = listed[0]["id"]

        with mock.patch.object(taskprov.LocalTaskProvider, "create", _counting_create):
            filed = await client.post(f"{BASE}/meetings/edit/tasks/file", json={"id": task_id})
            assert filed.status == 200, await filed.text()
            filed_ref = (await filed.json())["ref"]

            resp = await client.patch(
                f"{BASE}/meetings/edit/tasks",
                json={
                    "id": task_id,
                    "fields": {
                        "review_status": k.REVIEW_PENDING,
                        "filed_ref": None,
                        "assignee": "Bob",
                    },
                },
            )
            assert resp.status == 200, await resp.text()
            body = await resp.json()

            again = await client.post(f"{BASE}/meetings/edit/tasks/file", json={"id": task_id})
            assert again.status == 200, await again.text()
            again_ref = (await again.json())["ref"]

        final = (await (await client.get(f"{BASE}/meetings/edit/tasks")).json())["tasks"]

    assert len(created) == 1, f"the provider created {len(created)} items, not 1"
    task = next(t for t in final if t["id"] == task_id)
    assert task["review_status"] == k.REVIEW_PUSHED
    assert task["filed_ref"]["id"] == filed_ref["id"]
    assert again_ref["id"] == filed_ref["id"]
    assert body["task"]["review_status"] == k.REVIEW_PUSHED
    assert body["task"]["assignee"] == "Bob", "the other field in the same edit was dropped"
    assert body["ignored_fields"] == ["filed_ref", "review_status"]


@pytest.mark.asyncio
async def test_a_plain_field_edit_reports_no_ignored_fields(app: web.Application) -> None:
    async with client_for(app) as client:
        await client.post(f"{BASE}/meetings/plain/init", json={})
        added = await client.post(f"{BASE}/meetings/plain/tasks", json={"description": "d"})
        task_id = (await added.json())["task"]["id"]
        resp = await client.patch(
            f"{BASE}/meetings/plain/tasks",
            json={"id": task_id, "fields": {"priority": "low"}},
        )
        body = await resp.json()

    assert resp.status == 200
    assert body["task"]["priority"] == "low"
    assert "ignored_fields" not in body
