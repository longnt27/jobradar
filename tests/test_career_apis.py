import asyncio

import httpx
import pytest

from job_radar.career_apis import collect_json_board
from job_radar.collectors import _expired_posting


DESCRIPTION = "Develop reliable Python and machine learning services with model evaluation, monitoring, and reproducible production deployments. " * 2


def test_json_board_follows_server_page_size_and_keeps_full_requirements(monkeypatch):
    requested = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            requested.append(params["from"])
            offset = params["from"]
            row = {"id": offset + 1, "name": "AI Engineer", "seo": {"url": f"/jobs/{offset + 1}"},
                   "description": DESCRIPTION, "category": {"requirements": ["Python", "Model evaluation"]},
                   "country": [{"name": "Viet Nam" if offset == 0 else "USA"}], "created": "2026-10-01T12:00:00Z"}
            return httpx.Response(200, json={"jobs": [row], "total": 2}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    source = {"url": "https://example.org/careers", "name": "Example careers", "employer_name": "Example", "config": {
        "api_url": "https://example.org/api/jobs", "offset_param": "from", "limit_param": "size",
        "rows_path": "jobs", "total_field": "total", "title_field": "name", "url_field": "seo.url",
        "description_sections": {"Description": "description", "Requirements": "category.requirements"},
        "location_fields": ["country"], "location_include": "Viet Nam", "published_field": "created",
    }}
    jobs = asyncio.run(collect_json_board(source))
    assert requested == [0, 1]
    assert len(jobs) == 1
    assert jobs[0].url == "https://example.org/jobs/1" and jobs[0].company == "Example"
    assert "Requirements\n\n• Python\n• Model evaluation" in jobs[0].description
    assert jobs[0].published_at == "2026-10-01T12:00:00+00:00"


@pytest.mark.parametrize("response", [{"unexpected": []}, {"jobs": ["broken"]}, {"jobs": [], "total": 5}])
def test_json_board_does_not_report_malformed_or_incomplete_listing_as_empty(monkeypatch, response):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            return httpx.Response(200, json=response, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError):
        asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
            "api_url": "https://example.org/api", "rows_path": "jobs", "total_field": "total",
            "title_field": "title", "url_field": "url",
        }}))


def test_wordpress_job_terms_deadline_and_detail_content(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            if "/wp-json/" in url:
                row = {"id": 42, "title": {"rendered": "AI Engineer &amp; Researcher"},
                       "link": "https://example.org/job/ai-engineer", "content": {"rendered": f"<p>{DESCRIPTION}</p>"},
                       "_embedded": {"wp:term": [[{"taxonomy": "location", "name": "Hanoi - Vietnam"}],
                                                [{"taxonomy": "workplace-type", "name": "Hybrid"}]]}}
                return httpx.Response(200, json=[row], headers={"X-WP-Total": "1"}, request=httpx.Request("GET", url))
            return httpx.Response(200, text="<main>Closing date: 31.12.2099</main>", request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/jobs-finder", "name": "Example", "config": {
        "api_url": "https://example.org/wp-json/wp/v2/job", "title_field": "title.rendered", "url_field": "link",
        "description_sections": {"": "content.rendered"}, "location_taxonomy": "location", "mode_taxonomy": "workplace-type",
        "location_include": "Vietnam", "detail_selector": "main",
    }}))
    assert jobs[0].title == "AI Engineer & Researcher"
    assert jobs[0].location == "Hanoi - Vietnam" and jobs[0].work_mode == "Hybrid"
    assert "Closing date" not in jobs[0].description
    assert _expired_posting("Closing date: 31.12.2020")
    assert not _expired_posting("Closing date: 31.12.2099")


def test_country_codes_open_deadlines_and_plain_text_requirements(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            rows = [{"id": i, "title": "AI Engineer", "slug": f"ai-{i}", "country": country,
                     "due": due, "mode": ["REMOTE", "HYBRID"], "description": DESCRIPTION,
                     "requirements": "Python programming\nModel evaluation"}
                    for i, country, due in [(1, "vn", "2099-12-31"), (2, "vn", "2000-01-01"), (3, "jp", "2099-12-31")]]
            return httpx.Response(200, json=rows, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
        "api_url": "https://example.org/api", "title_field": "title", "url_field": "url", "url_template": "https://example.org/jobs/{slug}",
        "description_sections": {"": "description", "Requirements": "requirements"}, "deadline_field": "due",
        "location_fields": ["country"], "location_include": "Vietnam", "mode_field": "mode",
        "value_maps": {"country": {"vn": "Vietnam", "jp": "Japan"}, "mode": {"REMOTE": "Remote", "HYBRID": "Hybrid"}},
    }}))
    assert len(jobs) == 1 and jobs[0].url == "https://example.org/jobs/ai-1"
    assert jobs[0].location == "Vietnam" and jobs[0].work_mode == "Remote, Hybrid"
    assert "Python programming\nModel evaluation" in jobs[0].description


def test_separate_detail_api_preserves_subsidiary_identity_and_custom_title(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            if url.endswith("/search"):
                data = {"items": [{"id": 357, "title": "Research Specialist", "titleCustom": "Senior AI Engineer", "companyCode": "VF"}], "total": 1}
            else:
                assert params == {"id": "357"}
                data = {"id": 357, "jobDescription": DESCRIPTION, "jobRequirement": "Python\nModel evaluation", "companyCode": "VF"}
            return httpx.Response(200, json={"data": data}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/jobs", "name": "Group", "employer_name": "Group", "config": {
        "api_url": "https://example.org/search", "rows_path": "data.items", "total_field": "data.total",
        "title_field": "title", "title_fields": ["titleCustom", "title"], "url_field": "url", "url_template": "https://example.org/jobs/{id}",
        "detail_api_url": "https://example.org/detail", "detail_api_params": {"id": "{id}"}, "detail_rows_path": "data",
        "description_sections": {"Responsibilities": "jobDescription", "Requirements": "jobRequirement"},
        "company_field": "companyCode", "value_maps": {"companyCode": {"VF": "VinFast"}},
    }}))
    assert len(jobs) == 1 and jobs[0].company == "VinFast"
    assert jobs[0].title == "Senior AI Engineer" and jobs[0].url == "https://example.org/jobs/357"
    assert "Requirements\n\nPython\nModel evaluation" in jobs[0].description


def test_public_feed_excludes_unlisted_postings_and_keeps_secondary_vietnam_locations(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            rows = [{"id": i, "title": "AI Engineer", "url": f"https://example.org/job/{i}", "isListed": listed,
                     "location": "Singapore", "secondaryLocations": [{"location": "Vietnam"}], "description": DESCRIPTION}
                    for i, listed in [(1, True), (2, False)]]
            return httpx.Response(200, json=rows, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
        "api_url": "https://example.org/api", "title_field": "title", "url_field": "url", "required_fields": {"isListed": True},
        "description_sections": {"": "description"}, "location_fields": ["location", "secondaryLocations"], "location_include": "Vietnam",
    }}))
    assert len(jobs) == 1 and jobs[0].location == "Singapore, Vietnam"


def test_greenhouse_html_entities_remain_readable_paragraphs_and_bullets(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            row = {"id": 1, "title": "AI Engineer", "url": "https://example.org/job/1",
                   "content": "&lt;p&gt;" + DESCRIPTION + "&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python &amp; SQL&lt;/li&gt;&lt;/ul&gt;"}
            return httpx.Response(200, json=[row], request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
        "api_url": "https://example.org/api", "title_field": "title", "url_field": "url", "description_sections": {"": "content"},
    }}))
    assert "• Python & SQL" in jobs[0].description
    assert "<p>" not in jobs[0].description


def test_nested_api_rows_and_array_fields_produce_specific_posting_urls(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            row = {"id": 9, "contents": [{"title": "Data Engineer", "slug": "data-engineer", "content": DESCRIPTION}],
                   "addresses": [{"provinceName": "Hà Nội"}]}
            return httpx.Response(200, json={"items": [{"data": row}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
        "api_url": "https://example.org/api", "rows_path": "items", "row_path": "data", "title_field": "contents.0.title", "url_field": "url",
        "url_template": "https://example.org/jobs/{slug}", "template_fields": {"slug": "contents.0.slug"},
        "description_sections": {"": "contents.0.content"}, "location_fields": ["addresses"],
    }}))
    assert len(jobs) == 1 and jobs[0].url == "https://example.org/jobs/data-engineer"
    assert jobs[0].title == "Data Engineer" and jobs[0].location == "Hà Nội"


def test_api_finder_pagination_uses_actual_offsets_in_query_templates(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            offset = int(params["finder"].split("offset=")[-1])
            row = {"id": offset + 1, "title": "AI Engineer", "url": f"https://example.org/job/{offset}", "content": DESCRIPTION}
            return httpx.Response(200, json={"items": [{"jobs": [row], "total": 2}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
        "api_url": "https://example.org/api", "rows_path": "items.0.jobs", "total_field": "items.0.total", "pagination": "offset",
        "params_templates": {"finder": "findReqs;limit={limit},offset={offset}"}, "title_field": "title", "url_field": "url",
        "description_sections": {"": "content"},
    }}))
    assert len(jobs) == 2 and [j.external_id for j in jobs] == ["1", "2"]


def test_zero_based_api_pagination_includes_first_page(monkeypatch):
    requests = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            page = params["page"]
            requests.append(page)
            row = {"id": page, "title": "AI Engineer", "url": f"https://example.org/jobs/{page}", "description": DESCRIPTION}
            return httpx.Response(200, json={"content": [row], "totalElements": 2}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
        "api_url": "https://example.org/api", "page_param": "page", "page_start": 0, "rows_path": "content", "total_field": "totalElements",
        "title_field": "title", "url_field": "url", "description_sections": {"": "description"},
    }}))
    assert requests == [0, 1] and len(jobs) == 2


def test_overlap_cannot_hide_missing_jobs_from_advertised_total(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            ids = [1, 2] if params["page"] == 1 else [2, 3]
            rows = [{"id": i, "title": "AI Engineer", "url": f"https://example.org/jobs/{i}", "description": DESCRIPTION} for i in ids]
            return httpx.Response(200, json={"jobs": rows, "total": 4}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError, match="advertised"):
        asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
            "api_url": "https://example.org/api", "page_param": "page", "rows_path": "jobs", "total_field": "total",
            "title_field": "title", "url_field": "url", "description_sections": {"": "description"},
        }}))
