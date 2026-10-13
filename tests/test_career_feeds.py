import asyncio
import json
from pathlib import Path

import httpx
import pytest

from bs4 import BeautifulSoup

from job_radar.collectors import _application_destination, _expired_posting, _target_title, collect_html_board
from job_radar.db import Database
from job_radar.employer_scope import HCMC_BASED
from job_radar.feed_catalog import CAREER_FEEDS
from job_radar.ranking import score_job
from job_radar.seeds import seed


def test_company_feeds_are_seeded_idempotently(tmp_path: Path) -> None:
    assert len(CAREER_FEEDS) == len({feed.employer for feed in CAREER_FEEDS}) >= 60
    assert {
        "VinDynamics", "VinRobotics", "VinMotion", "Techcombank", "Vietcombank",
        "MB Bank", "VPBank", "GPBank", "BIDV", "VNPT AI", "CMC Telecom",
        "PVcomBank", "SHB", "NCB", "KienlongBank", "One Mount", "SmartOSC",
        "NVIDIA", "Shopee", "SPX Express", "Vinschool", "FPT University", "9Pay",
        "SSI", "TCBS", "FPT Smart Cloud", "Viettel High Tech", "Viettel Cyber Security",
    } <= {feed.employer for feed in CAREER_FEEDS}
    assert not {feed.employer for feed in CAREER_FEEDS} & HCMC_BASED.keys()
    assert all("linkedin.com" not in feed.url and "facebook.com" not in feed.url for feed in CAREER_FEEDS)

    db = Database(tmp_path / "jobs.db")
    seed(db)
    seed(db)
    rows = db.all("SELECT s.name,s.interval_minutes,s.config,e.name AS employer FROM sources s JOIN employers e ON e.id=s.employer_id WHERE s.kind='career'")
    assert len(rows) == len(CAREER_FEEDS)
    assert {row["employer"] for row in rows} == {feed.employer for feed in CAREER_FEEDS}
    assert all(row["interval_minutes"] == 1440 and '"adapter"' in row["config"] for row in rows)

    cmc = next(row for row in rows if row["employer"] == "CMC Global")
    assert json.loads(cmc["config"])["title_include"]
    db.execute("UPDATE sources SET interval_minutes=240 WHERE kind='career'")
    seed(db)
    assert {row["interval_minutes"] for row in db.all("SELECT interval_minutes FROM sources WHERE kind='career'")} == {1440}


def test_cmc_uses_card_titles_to_filter_before_detail_limit(monkeypatch) -> None:
    cmc = next(feed for feed in CAREER_FEEDS if feed.employer == "CMC Global")
    config = {"adapter": cmc.adapter, **cmc.options, "max_pages": 2, "max_results": 1}
    listing = "".join(
        f'<li class="__careers-post-wrapper"><h3>Frontend Developer {index}</h3>'
        f'<a href="/career/frontend-{index}/">Learn more</a></li>'
        for index in range(120)
    )
    second_page = '<li class="__careers-post-wrapper"><h3>AI Engineer</h3><a href="/career/ai-engineer/">Learn more</a></li>'
    requested: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            requested.append(url)
            if url.endswith("/career/"):
                html = f"<main>{listing}</main>"
            elif url.endswith("/career/page/2/"):
                html = f"<main>{second_page}</main>"
            else:
                html = "<main><h1>AI Engineer</h1><p>" + "Build and deploy AI systems with Python. " * 8 + "</p></main>"
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board({"url": cmc.url, "name": "CMC Global careers", "config": config}))
    assert [job.title for job in jobs] == ["AI Engineer"]
    assert requested == [cmc.url, "https://cmcglobal.com.vn/career/page/2/",
                         "https://cmcglobal.com.vn/career/ai-engineer/"]


def test_expired_requisition_and_non_job_heading_are_rejected() -> None:
    assert _expired_posting("Senior AI Engineer Application deadline 14/11 — 14/11/2025")
    assert not _expired_posting("AI Engineer Application deadline 10/09 — 10/10/2099")
    assert not _expired_posting("Hạn nộp hồ sơ 28/09/2026 - 28/10/2099")
    assert _target_title("Senior Data Engineer")
    assert not _target_title("Careers")
    assert not _target_title("We build AI systems and seek an engineer. " * 10)


def test_vncs_global_research_role_uses_only_career_cards_and_post_content(monkeypatch) -> None:
    feed = next(feed for feed in CAREER_FEEDS if feed.employer == "VNCS Global")
    title = "NHÂN VIÊN NGHIÊN CỨU & PHÁT TRIỂN (R&D)"
    posting_url = "https://vncsglobal.vn/nhan-vien-nghien-cuu-phat-trien-rd/"
    description = "Nghiên cứu và xây dựng AI Agent dựa trên LLM cho AI Pentest và AI SOC. " * 3
    requested: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            requested.append(url)
            if url == feed.url:
                page = (
                    '<a href="/ai-solutions/">AI security solutions</a>'
                    '<article class="elementor-post"><h3 class="elementor-post__title">'
                    f'<a href="{posting_url}">{title}</a></h3>'
                    f'<a href="{posting_url}">Xem chi tiết</a></article>'
                    '<article class="elementor-post"><h3 class="elementor-post__title">'
                    '<a href="/marketing/">Marketing Executive</a></h3></article>'
                )
            else:
                assert url == posting_url
                page = (
                    f'<h1>{title}</h1><article>{"Unrelated news. " * 20}</article>'
                    f'<div class="elementor-widget-theme-post-content"><p>{description}</p>'
                    '<a href="mailto:jobs@vncsglobal.vn">Gửi CV ứng tuyển</a></div>'
                )
            return httpx.Response(200, text=page, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board({
        "url": feed.url, "name": "VNCS Global careers", "employer_name": feed.employer,
        "config": {"adapter": feed.adapter, **feed.options},
    }))
    assert requested == [feed.url, posting_url]
    assert len(jobs) == 1
    assert jobs[0].title == title and jobs[0].company == "VNCS Global"
    assert description.strip() in jobs[0].description
    assert "Unrelated news" not in jobs[0].description
    assert jobs[0].apply_url == "mailto:jobs@vncsglobal.vn"


def test_company_publication_time_without_timezone_can_be_ranked() -> None:
    score, detail = score_job({
        "title": "AI Engineer", "description": "Build robot perception models.",
        "location": "Hanoi", "published_at": "2026-03-31T20:53:33",
    }, {})
    assert score > 0
    assert detail["components"]["freshness"] >= 0


def test_explicit_location_selector_does_not_borrow_related_job_locations(monkeypatch) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            if url.endswith("/careers"):
                page = '<a href="/job/42">AI Engineer</a>'
            else:
                page = '<h1>AI Engineer</h1><main class="posting"><p>' + "Build AI systems with Python. " * 10 + '</p></main><aside class="location">Tokyo</aside>'
            return httpx.Response(200, text=page, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board({"url": "https://example.org/careers", "name": "Example", "config": {
        "link_path": r"^/job/\d+$", "description_selector": ".posting", "location_selector": ".posting .location",
    }}))
    assert len(jobs) == 1 and jobs[0].location == ""


def test_career_application_destination_prefers_apply_link_email_then_posting() -> None:
    posting = "https://example.org/jobs/42"
    linked = BeautifulSoup('<a href="/apply/42">Apply now</a><p>Email jobs@example.org</p>', "html.parser")
    assert _application_destination(linked, posting) == "https://example.org/apply/42"
    emailed = BeautifulSoup('<p>To apply, send your CV to careers@example.org</p>', "html.parser")
    assert _application_destination(emailed, posting) == "mailto:careers@example.org"
    assert _application_destination(BeautifulSoup("<p>Job details</p>", "html.parser"), posting) == posting


def test_role_matches_vietnamese_banking_and_tech_titles() -> None:
    assert _target_title("Chuyên viên cao cấp Phân tích dữ liệu rủi ro")
    assert _target_title("Kỹ sư Dữ liệu")
    assert _target_title("Chuyên viên Quản trị dữ liệu")
    assert _target_title("Chuyên viên Mô hình Quản trị Rủi ro Tín dụng")
    assert _target_title("Chuyên viên cao cấp quản lý Tài chính công nghệ (Cloud Finance Management) - K.CNTT")
    assert not _target_title("Chuyên viên Quan hệ Khách hàng Doanh nghiệp")
    assert not _target_title("Giao dịch viên")


def test_html_board_prefers_target_title_over_generic_anchor(monkeypatch) -> None:
    source = {
        "url": "https://example.com/careers",
        "name": "Test careers",
        "config": {
            "link_path": r"^/job/\d+$",
            "max_results": 2,
        },
    }
    html_page = (
        '<main>'
        '<a href="/job/101">Ứng tuyển</a>'
        '<a href="/job/101">Chuyên viên Phân tích dữ liệu</a>'
        '<a href="/job/102">Non-tech Role</a>'
        '<a href="/job/103">AI Engineer</a>'
        '</main>'
    )
    detail_101 = '<main><h1>Chuyên viên Phân tích dữ liệu</h1><p>' + 'Xử lý dữ liệu lớn với Python và SQL. ' * 10 + '</p></main>'
    detail_103 = '<main><h1>AI Engineer</h1><p>' + 'Xây dựng mô hình máy học với PyTorch. ' * 10 + '</p></main>'

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            if url.endswith("/careers"):
                text = html_page
            elif url.endswith("/job/101"):
                text = detail_101
            elif url.endswith("/job/103"):
                text = detail_103
            else:
                text = "<main><h1>Unknown</h1></main>"
            return httpx.Response(200, text=text, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board(source))
    titles = [job.title for job in jobs]
    assert "Chuyên viên Phân tích dữ liệu" in titles
    assert "AI Engineer" in titles
    assert len(jobs) == 2


def test_vpbank_collector_processes_rmk_api_and_details(monkeypatch) -> None:
    from job_radar.collectors import collect_vpbank

    source = {
        "employer_name": "VPBank",
        "name": "VPBank careers",
        "url": "https://tuyendung.vpbank.com.vn/",
        "config": {"queries": ["AI"], "max_pages": 1, "max_results": 5},
    }
    api_response = {
        "jobSearchResult": [
            {
                "response": {
                    "id": "12167",
                    "unifiedStandardTitle": "CVCC Khoa học dữ liệu - AI Engineer - Hà Nội - TA105",
                    "unifiedUrlTitle": "CVCC-Khoa-hoc-du-lieu-AI-Engineer",
                    "jobLocationShort": ["Hà Nội, VNM"],
                }
            },
            {
                "response": {
                    "id": "99999",
                    "unifiedStandardTitle": "Nhân viên Telesales",
                    "unifiedUrlTitle": "Telesales",
                    "jobLocationShort": ["Hà Nội, VNM"],
                }
            },
        ]
    }
    detail_html = (
        '<html><body>'
        '<div class="joblayouttoken">'
        'Mô tả công việc<br>CVCC Khoa học dữ liệu - AI Engineer - Hà Nội<br>'
        'Phát triển mô hình trí tuệ nhân tạo và học máy cho ngân hàng.<br>' + 'Nhiệm vụ chính bao gồm huấn luyện mô hình. ' * 10 +
        '</div></body></html>'
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, **kwargs):
            return httpx.Response(200, json=api_response, request=httpx.Request("POST", url))

        async def get(self, url, **kwargs):
            return httpx.Response(200, text=detail_html, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_vpbank(source))
    assert len(jobs) == 1
    assert jobs[0].title == "CVCC Khoa học dữ liệu - AI Engineer - Hà Nội - TA105"
    assert jobs[0].company == "VPBank"
    assert jobs[0].external_id == "12167"
    assert "https://tuyendung.vpbank.com.vn/job/CVCC-Khoa-hoc-du-lieu-AI-Engineer/12167-vi_VN" in jobs[0].url
    assert "Phát triển mô hình trí tuệ nhân tạo" in jobs[0].description


def test_workatsea_collector_processes_ats_api(monkeypatch) -> None:
    from job_radar.collectors import collect_workatsea

    source = {
        "employer_name": "Shopee",
        "name": "Shopee careers",
        "url": "https://careers.shopee.vn/jobs",
        "config": {},
    }
    api_response = {
        "code": 0,
        "data": {
            "job_list": [
                {
                    "id": 101,
                    "job_name": "Senior Software Engineer - AI / Machine Learning",
                    "job_description": "<p>Design and deploy deep learning models for recommendations.</p>" * 5,
                    "requirements": "<p>5+ years of experience with PyTorch and distributed training.</p>" * 5,
                },
                {
                    "id": 102,
                    "job_name": "SPX - Operations Lead",
                    "job_description": "<p>Operations management.</p>",
                    "requirements": "<p>Degree in Logistics.</p>",
                },
                {
                    "id": 103,
                    "job_name": "Customer Support Agent",
                    "job_description": "<p>Customer support calls.</p>",
                    "requirements": "<p>Customer support skills.</p>",
                },
            ]
        }
    }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, **kwargs):
            return httpx.Response(200, json=api_response, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_workatsea(source))
    assert len(jobs) == 1
    assert jobs[0].title == "Senior Software Engineer - AI / Machine Learning"
    assert jobs[0].company == "Shopee"
    assert jobs[0].external_id == "101"
    assert jobs[0].url == "https://careers.shopee.vn/job-detail/101"
    assert "Design and deploy deep learning models" in jobs[0].description

    spx_source = {
        "employer_name": "SPX Express",
        "name": "SPX Express careers",
        "url": "https://careers.shopee.vn/jobs",
        "config": {"company_filter": "SPX"},
    }
    api_response_spx = {
        "code": 0,
        "data": {
            "job_list": [
                {
                    "id": 201,
                    "job_name": "SPX - Lead Software Engineer, Logistics Optimization",
                    "job_description": "<p>Architect large-scale logistics routing and tracking systems with Python and Golang.</p>" * 5,
                    "requirements": "<p>Proficiency in algorithmic graph optimization and cloud services.</p>" * 5,
                },
                {
                    "id": 202,
                    "job_name": "Lead Software Engineer, Fintech",
                    "job_description": "<p>Fintech systems.</p>" * 5,
                    "requirements": "<p>Fintech experience.</p>" * 5,
                },
            ]
        }
    }

    class SpxClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, **kwargs):
            return httpx.Response(200, json=api_response_spx, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", SpxClient)
    spx_jobs = asyncio.run(collect_workatsea(spx_source))
    assert len(spx_jobs) == 1
    assert spx_jobs[0].title == "SPX - Lead Software Engineer, Logistics Optimization"
    assert spx_jobs[0].company == "SPX Express"
    assert spx_jobs[0].external_id == "201"


def test_workday_collector_queries_cxs_and_retrieves_detail(monkeypatch) -> None:
    from job_radar.collectors import collect_workday

    source = {
        "employer_name": "NVIDIA",
        "name": "NVIDIA careers",
        "url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        "config": {
            "search_url": "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
            "detail_base": "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite",
            "search_text": "Vietnam",
        },
    }
    search_json = {
        "jobPostings": [
            {
                "title": "Senior Solutions Architect - Generative AI",
                "externalPath": "/job/Vietnam/Senior-Solutions-Architect---GenAI_JR198",
                "locationsText": "Vietnam, Hanoi",
            },
            {
                "title": "Regional Sales Manager",
                "externalPath": "/job/Vietnam/Sales-Manager_JR199",
                "locationsText": "Vietnam, Hanoi",
            },
        ]
    }
    detail_json = {
        "jobPostingInfo": {
            "jobReqId": "JR198",
            "jobDescription": "<p>Accelerate generative AI adoption across enterprise clients using NVIDIA GPUs and NeMo stack.</p>" * 5,
            "externalUrl": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/Vietnam/Senior-Solutions-Architect_JR198",
        }
    }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, **kwargs):
            return httpx.Response(200, json=search_json, request=httpx.Request("POST", url))

        async def get(self, url, **kwargs):
            return httpx.Response(200, json=detail_json, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_workday(source))
    assert len(jobs) == 1
    assert jobs[0].title == "Senior Solutions Architect - Generative AI"
    assert jobs[0].company == "NVIDIA"
    assert jobs[0].external_id == "JR198"
    assert jobs[0].location == "Vietnam, Hanoi"
    assert "enterprise clients using NVIDIA GPUs" in jobs[0].description


def test_workday_paginates_country_facets_and_retains_vietnam_additional_locations(monkeypatch) -> None:
    from job_radar.collectors import collect_workday
    offsets = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, json):
            assert json["appliedFacets"] == {"Country": ["VN"]}
            offset = json["offset"]
            offsets.append(offset)
            return httpx.Response(200, json={"total": 2 if offset == 0 else 0, "jobPostings": [{
                "title": "AI Engineer", "externalPath": f"/job/Global/AI_{offset}", "locationsText": "3 Locations",
            }]}, request=httpx.Request("POST", url))

        async def get(self, url):
            return httpx.Response(200, json={"jobPostingInfo": {
                "jobReqId": url.rsplit("_", 1)[-1], "location": "Singapore", "additionalLocations": ["Vietnam, Hanoi"],
                "jobDescription": "<p>Develop production AI services with Python and model evaluation.</p>" * 5,
                "startDate": "2026-10-01",
            }}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_workday({"url": "https://example.org/Careers", "name": "Example", "config": {
        "search_url": "https://example.org/api/jobs", "detail_base": "https://example.org/api", "applied_facets": {"Country": ["VN"]},
    }}))
    assert offsets == [0, 1] and len(jobs) == 2
    assert jobs[0].url == "https://example.org/Careers/job/Global/AI_0"
    assert jobs[0].location == "Singapore, Vietnam, Hanoi"
    assert jobs[0].published_at == "2026-10-01"


def test_successfactors_search_pagination_preserves_country_and_own_location(monkeypatch) -> None:
    from job_radar.collectors import collect_successfactors
    requested = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            requested.append(url)
            if "/job/" in url:
                page = '<h1>AI Engineer</h1><span data-office="true">Hanoi, Vietnam</span><div class="location">Search by Location</div><div class="jobdescription">' + "Build AI services with Python. " * 10 + '</div>'
            elif "startrow=50" in url:
                page = '<main>No jobs</main>'
            elif "startrow=25" in url:
                page = '<a class="jobTitle-link" href="/job/ai/42">AI Engineer</a>'
            else:
                page = '<a class="jobTitle-link" href="/job/sales/1">Sales Manager</a>'
            return httpx.Response(200, text=page, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_successfactors({"url": "https://example.org/search/?locationsearch=Viet+Nam", "name": "Example", "config": {
        "max_pages": 4, "search_page_size": 25, "location_selector": "[data-office]",
    }}))
    assert len(jobs) == 1 and jobs[0].location == "Hanoi, Vietnam"
    assert all("locationsearch=Viet+Nam" in url for url in requested if "/job/" not in url)
    assert any("startrow=25" in url for url in requested)


@pytest.mark.parametrize("employer", ["MB Ageas Life", "Hyundai Motor Vietnam"])
def test_talentnetwork_detail_extracts_own_requirements_and_deadline(monkeypatch, employer):
    feed = next(f for f in CAREER_FEEDS if f.employer == employer)
    posting = feed.url.split("/job-search/")[0] + "/job/data-engineer.35ac102e.html"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            if url == feed.url:
                page = f'<a href="{posting}">Data Engineer</a>'
            elif "/job/" not in url:
                page = "<main>Listing ended</main>"
            else:
                page = ('<h1>Data Engineer</h1><div class="metadata-list">'
                        '<div class="metadata-list_section metadata-list_section--blue"><h4>Work Location</h4><ul class="metadata-list_items"><li>Hà Nội</li></ul></div>'
                        '<div class="metadata-list_section"><h4>Deadline to Apply</h4><ul><li>31/12/2099</li></ul></div></div>'
                        '<div class="job-post-description"><h2>Job Description</h2><p>'
                        + "Develop production data pipelines with Python. " * 5
                        + '</p><h2>Job Requirement</h2><p>SQL and reproducible model evaluation.</p></div>'
                        '<aside>Expired Date: 01/01/2000 Related job requirements.</aside>')
            return httpx.Response(200, text=page, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board({"url": feed.url, "name": employer, "employer_name": employer, "config": feed.options}))
    assert len(jobs) == 1 and jobs[0].location == "Hà Nội"
    assert "Job Requirement" in jobs[0].description and "SQL" in jobs[0].description
    assert "Related job" not in jobs[0].description


def test_seed_retains_custom_career_sources_and_their_observation_history(tmp_path):
    from job_radar.db import new_id, now
    from job_radar.ingest import ObservedJob, ingest

    db = Database(tmp_path / "jobs.db")
    seed(db)
    employer = db.one("SELECT id FROM employers WHERE name='FPT Software'")["id"]
    source_id = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,employer_id,config,created_at) VALUES(?,'career',?,?,?,?,?)",
               (source_id, "FPT R&D careers", "https://fpt.example/rd/jobs", employer, '{"adapter":"legacy"}', now()))
    vacancy_id, _ = ingest(db, source_id, ObservedJob("https://fpt.example/rd/jobs/42", "AI Engineer", "FPT Software", "Build AI services and evaluate production models. " * 5))
    seed(db)
    assert db.one("SELECT url,config FROM sources WHERE id=?", (source_id,)) == {"url": "https://fpt.example/rd/jobs", "config": '{"adapter":"legacy"}'}
    assert db.one("SELECT COUNT(*) AS n FROM observations WHERE source_id=?", (source_id,))["n"] == 1
    assert db.one("SELECT id FROM vacancies WHERE id=?", (vacancy_id,))

