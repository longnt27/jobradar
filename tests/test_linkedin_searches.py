from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.linkedin_searches import search_from_url
from job_radar.settings import Settings
from job_radar.web import create_app


def test_linkedin_search_link_provides_name_and_stable_url() -> None:
    assert search_from_url(
        "https://www.linkedin.com/jobs/search-results/?location=Hanoi%2C+Vietnam&"
        "currentJobId=123&keywords=Data+Analyst&f_TPR=r86400&trackingId=ignored"
    ) == (
        "https://www.linkedin.com/jobs/search/?f_TPR=r86400&keywords=Data+Analyst+in+Hanoi%2C+Vietnam",
        "Data Analyst · Hanoi, Vietnam",
    )


def test_linkedin_search_url_preserves_distinct_filters_and_reuses_existing_feed(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    old = next(source for source in client.get("/api/sources?kind=linkedin").json()
               if source["name"] == "AI Engineer")
    client.patch(f"/api/sources/{old['id']}", json={"enabled": False})
    added = client.post("/api/sources", json={
        "kind": "linkedin",
        "url": "https://www.linkedin.com/jobs/search-results/?location=Hanoi%2C+Vietnam&"
               "currentJobId=123&f_TPR=r86400&keywords=AI+Engineer",
    })
    assert added.status_code == 201, added.text
    assert added.json()["id"] != old["id"]
    assert not next(source for source in client.get("/api/sources?kind=linkedin").json()
                    if source["id"] == old["id"])["enabled"]
    repeated = client.post("/api/sources", json={
        "kind": "linkedin",
        "url": "https://www.linkedin.com/jobs/search/?keywords=AI+Engineer&location=Hanoi%2C+Vietnam&f_TPR=r86400",
    })
    assert repeated.json() == {"id": added.json()["id"], "name": added.json()["name"], "existing": True}
    fresh = client.post("/api/sources", json={
        "kind": "linkedin", "url": "https://www.linkedin.com/jobs/search/?keywords=Data+Analyst&location=Hanoi",
    })
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["name"] == "Data Analyst · Hanoi"
    assert client.post("/api/sources", json={
        "kind": "linkedin", "url": "https://www.linkedin.com/jobs/view/123/",
    }).status_code == 422
