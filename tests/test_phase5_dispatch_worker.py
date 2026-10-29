import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, Suppression, AuditLog
from app.pipeline.sending_controller import set_sending_state, get_sending_status
from app.pipeline.dispatch_worker import dispatch_worker

client = TestClient(app)

TEST_SEGMENT = "horeca_bakery"


def test_worker_status_endpoint():
    res = client.get("/api/queue/worker-status")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "worker_alive" in data
    assert "provider_class" in data
    assert "live_email_sending" in data
    assert "is_processing" in data


def test_dispatch_next_blocked_when_sending_stopped():
    with get_db_session() as session:
        # Pause sending
        set_sending_state(session, active=False, operator_name="pytest_tester")

    try:
        res = client.post("/api/queue/dispatch-next")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "ok"
        assert "result" in data
        assert data["result"]["status"] == "sending_stopped"
    finally:
        with get_db_session() as session:
            # Restore sending
            set_sending_state(session, active=True, operator_name="pytest_tester")


def test_dispatch_batch_respects_stopped_sending():
    with get_db_session() as session:
        set_sending_state(session, active=False, operator_name="pytest_tester")

    try:
        res = client.post("/api/queue/dispatch-batch", json={"limit": 3})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "ok"
        # Since sending is stopped, each item tried returns sending_stopped and batch breaks
        assert len(data["results"]) <= 1
        if data["results"]:
            assert data["results"][0]["status"] == "sending_stopped"
    finally:
        with get_db_session() as session:
            set_sending_state(session, active=True, operator_name="pytest_tester")


def test_dispatch_next_no_tasks_available():
    # If no tasks are eligible, dispatch returns no_task or status
    with patch("app.pipeline.dispatch_worker.run_single_pipeline_item", return_value=None):
        res = client.post("/api/queue/dispatch-next")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "no_task"
        assert "No eligible tasks" in data["message"]


def test_dispatch_batch_with_mocked_runner():
    mock_result = {
        "queue_item_id": 999,
        "contact_id": 111,
        "email": "test@example.com",
        "status": "sent",
        "send_key": "test_send_key_123",
    }
    with patch("app.pipeline.dispatch_worker.run_pipeline_batch", return_value=[mock_result, mock_result]):
        res = client.post("/api/queue/dispatch-batch", json={"limit": 2})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "ok"
        assert data["dispatched_count"] == 2
        assert len(data["results"]) == 2
