from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.facebook_groups import clean_group_title, group_from_url
from job_radar.settings import Settings
from job_radar.web import create_app


def test_group_links_produce_a_canonical_url_and_readable_name() -> None:
    assert group_from_url("https://m.facebook.com/groups/AIJobsVietnam/posts/123?ref=share") == (
        "https://www.facebook.com/groups/AIJobsVietnam/", "AI Jobs Vietnam")
    assert group_from_url("https://www.facebook.com/groups/123456789/?ref=share") == (
        "https://www.facebook.com/groups/123456789/", "Facebook group 123456789")
    assert clean_group_title("Vietnam AI Jobs | Facebook") == "Vietnam AI Jobs"
    assert clean_group_title("(17) Vietnam AI Jobs | Facebook") == "Vietnam AI Jobs"
    assert clean_group_title("Chats") is None
    assert clean_group_title("Log in to Facebook") is None


def test_facebook_group_needs_only_url_and_uses_page_name_when_available(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.set_setting("social_login_completed_at_facebook", "2026-10-01T12:00:00+00:00")
    (tmp_path / "browser-profile").mkdir()
    looked_up = []

    async def lookup(_settings, url):
        looked_up.append(url)
        return "Vietnam AI Jobs"

    monkeypatch.setattr("job_radar.web.lookup_facebook_group_name", lookup)
    client = TestClient(app)
    added = client.post("/api/sources", json={
        "kind": "facebook", "url": "https://m.facebook.com/groups/AIJobsVietnam/posts/123?ref=share"})
    assert added.status_code == 201, added.text
    assert added.json()["name"] == "Vietnam AI Jobs"
    assert looked_up == ["https://www.facebook.com/groups/AIJobsVietnam/"]
    source = next(item for item in client.get("/api/sources?kind=facebook").json() if item["id"] == added.json()["id"])
    assert source["url"] == looked_up[0]
    assert source["name"] == "Vietnam AI Jobs"
    app.state.db.execute("UPDATE sources SET url=?,enabled=0 WHERE id=?", (
        "https://m.facebook.com/groups/AIJobsVietnam?ref=old", added.json()["id"]))
    duplicate = client.post("/api/sources", json={"kind": "facebook", "url": looked_up[0]})
    assert duplicate.json()["id"] == added.json()["id"]
    assert len(client.get("/api/sources?kind=facebook").json()) == 1
    assert client.get("/api/sources?kind=facebook").json()[0]["enabled"] == 1
    assert client.post("/api/sources", json={"kind": "facebook", "url": "https://example.com/groups/123"}).status_code == 422
    assert client.post("/api/sources", json={"kind": "career", "url": "https://example.com/jobs"}).status_code == 422
