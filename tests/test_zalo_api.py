from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.web import create_app
from test_zalo_contacts import facebook_job


def app_with_post(tmp_path):
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    job, observation = facebook_job(app.state.db, title="STUPID EXTRACTED TITLE")
    return app, job, observation


def mock_transport(monkeypatch, *, status="sent_confirmed"):
    calls = []
    async def lookup(_settings, phone):
        return {"status": "ready", "recipient": {"phone": phone, "display_name": "Recruiter", "identity_key": "friend-item-123", "evidence": "Unique phone search"}}
    async def send(_settings, recipient, message):
        calls.append({"recipient": recipient, "message": message})
        return {"status": status, "receipt": {"message_id": "message-123", "visible_status": "Đã gửi"} if status == "sent_confirmed" else {}, "error": None if status == "sent_confirmed" else "No confirmation"}
    monkeypatch.setattr("job_radar.zalo_api.lookup_zalo_recipient", lookup)
    monkeypatch.setattr("job_radar.zalo_api.send_zalo_inquiry", send)
    return calls


def prepare(client, job, observation):
    response = client.post(f"/api/jobs/{job}/zalo", json={"observation_id": observation})
    assert response.status_code == 200
    draft = response.json()
    response = client.post(f"/api/zalo-inquiries/{draft['id']}/lookup")
    assert response.status_code == 200
    return response.json()["draft"]


def test_approve_calls_transport_only_with_current_review_hash(tmp_path, monkeypatch):
    app, job, observation = app_with_post(tmp_path)
    calls = mock_transport(monkeypatch)
    client = TestClient(app)
    draft = prepare(client, job, observation)
    body = "Em thấy vị trí AI Engineer trên Facebook. Em có thể gửi CV qua Zalo cho anh/chị được không ạ?"
    edited = client.patch(f"/api/zalo-inquiries/{draft['id']}", json={"position": "AI Engineer", "message": body}).json()
    assert client.post(f"/api/zalo-inquiries/{draft['id']}/approve", json={"package_hash": draft["package_hash"]}).status_code == 409
    assert calls == []
    approved = client.post(f"/api/zalo-inquiries/{draft['id']}/approve", json={"package_hash": edited["package_hash"]})
    assert approved.status_code == 200
    assert approved.json()["attempt"]["status"] == "sent_confirmed"
    assert calls[0]["message"] == body
    assert len(calls) == 1
    assert client.get(f"/api/jobs/{job}").json()["application_progress"] == "not_started"
    assert client.post(f"/api/zalo-inquiries/{draft['id']}/approve", json={"package_hash": edited["package_hash"]}).status_code == 409


def test_phone_edit_disables_approval_until_new_lookup(tmp_path, monkeypatch):
    app, job, observation = app_with_post(tmp_path)
    calls = mock_transport(monkeypatch)
    client = TestClient(app)
    draft = prepare(client, job, observation)
    changed = client.patch(f"/api/zalo-inquiries/{draft['id']}", json={"phone": "0976543210"}).json()
    assert changed["recipient"] == {}
    assert not changed["send_ready"]
    assert client.post(f"/api/zalo-inquiries/{draft['id']}/approve", json={"package_hash": changed["package_hash"]}).status_code == 422
    assert calls == []


def test_failed_lookup_invalidates_previous_recipient(tmp_path, monkeypatch):
    app, job, observation = app_with_post(tmp_path)
    mock_transport(monkeypatch)
    client = TestClient(app)
    draft = prepare(client, job, observation)
    async def expired(*_):
        return {"status": "needs_attention", "code": "auth_required", "detail": "Sign in to Zalo"}
    monkeypatch.setattr("job_radar.zalo_api.lookup_zalo_recipient", expired)
    result = client.post(f"/api/zalo-inquiries/{draft['id']}/lookup").json()
    assert not result["draft"]["send_ready"]
    assert result["draft"]["recipient"] == {}
    assert app.state.db.get_setting("social_reauth_required_zalo") is True


def test_existing_custom_message_survives_template_change(tmp_path):
    app, job, observation = app_with_post(tmp_path)
    client = TestClient(app)
    draft = client.post(f"/api/jobs/{job}/zalo", json={"observation_id": observation}).json()
    client.patch(f"/api/zalo-inquiries/{draft['id']}", json={"message": "Em hỏi riêng ạ."})
    assert client.put("/api/setup/zalo-template", json={"template": "Em hỏi về {position} trong {group}."}).status_code == 200
    assert client.get(f"/api/zalo-inquiries/{draft['id']}").json()["message"] == "Em hỏi riêng ạ."
    new = client.post(f"/api/jobs/{job}/zalo", json={"observation_id": observation}).json()
    assert new["message"] == "Em hỏi về STUPID EXTRACTED TITLE trong AI Jobs Vietnam."
    assert client.put("/api/setup/zalo-template", json={"template": "{unsupported}"}).status_code == 422


def test_multiple_posts_require_explicit_source_selection(tmp_path):
    app, job, observation = app_with_post(tmp_path)
    other, foreign = facebook_job(app.state.db, group="Other group", title="Other position", text="Zalo 0976543210")
    client = TestClient(app)
    assert client.post(f"/api/jobs/{job}/zalo", json={"observation_id": foreign}).status_code == 404
    app.state.db.execute("UPDATE vacancy_observations SET vacancy_id=? WHERE observation_id=?", (job, foreign))
    context = client.get(f"/api/jobs/{job}/zalo").json()
    assert {row["group"] for row in context["candidates"]} == {"AI Jobs Vietnam", "Other group"}
    second = client.post(f"/api/jobs/{job}/zalo", json={"observation_id": foreign}).json()
    assert second["group"] == "Other group"
    assert second["phone"] == "0976543210"


def test_uncertain_send_requires_manual_resolution_then_fresh_lookup(tmp_path, monkeypatch):
    app, job, observation = app_with_post(tmp_path)
    calls = mock_transport(monkeypatch, status="send_uncertain")
    client = TestClient(app)
    draft = prepare(client, job, observation)
    first = client.post(f"/api/zalo-inquiries/{draft['id']}/approve", json={"package_hash": draft["package_hash"]}).json()
    assert first["attempt"]["status"] == "send_uncertain"
    assert client.post(f"/api/zalo-inquiries/{draft['id']}/approve", json={"package_hash": draft["package_hash"]}).status_code == 409
    path = f"/api/zalo-inquiries/{draft['id']}/attempts/{first['attempt']['id']}/resolve"
    assert client.post(path, json={"sent": False}).status_code == 200
    assert client.get(f"/api/zalo-inquiries/{draft['id']}").json()["recipient"] == {}
    assert len(calls) == 1
