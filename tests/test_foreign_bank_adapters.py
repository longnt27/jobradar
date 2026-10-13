import asyncio

import httpx
import pytest

from job_radar.foreign_bank_adapters import collect_public_bank, parse_public_bank


BOARD = "https://publicbank.com.vn/News/Career?cateId=22"
PARAGRAPH = "Tham gia xây dựng, nâng cấp và phát triển các hệ thống phần mềm ứng dụng, hỗ trợ người sử dụng và vận hành các dịch vụ ngân hàng."


def _fixture() -> str:
    return f"""<html><body>
      <h2>1. HỘI SỞ CHÍNH</h2>
      <p>Lập trình viên phòng Công nghệ thông tin, Hội sở chính</p>
      <ul><li>{PARAGRAPH}</li><li>Thành thạo Java hoặc Kotlin, Swift.</li></ul>
      <p>Chuyên viên an ninh mạng phòng Công nghệ thông tin, Hội sở chính</p>
      <ul><li>{PARAGRAPH}</li><li>Firewall, IPS, router, switch.</li></ul>
      <h2>2. CHI NHÁNH: Hà Nội và TP. Hồ Chí Minh</h2>
      <p>Chuyên viên phòng giao dịch khách hàng</p><ul><li>Giao dịch khách hàng.</li></ul>
      <h2>THÔNG TIN LIÊN HỆ:</h2><p>Email: hrdho@publicbank.com.vn</p>
    </body></html>"""


def test_public_bank_splits_roles_without_description_bleed():
    jobs = parse_public_bank(_fixture(), BOARD)
    assert len(jobs) == 2
    assert len({job.url for job in jobs}) == 2
    assert len({job.external_id for job in jobs}) == 2
    assert all(job.url.startswith(BOARD + "&jobradar_role=") for job in jobs)
    assert all(job.company == "Public Bank Vietnam" for job in jobs)
    assert all(job.apply_url == "mailto:hrdho@publicbank.com.vn" for job in jobs)
    assert all(job.location == "Hội sở chính, Hà Nội" for job in jobs)
    assert "Java" in jobs[0].description
    assert "Firewall" not in jobs[0].description
    assert "Firewall" in jobs[1].description
    assert "Swift" not in jobs[1].description
    assert [job.external_id for job in parse_public_bank(_fixture(), BOARD)] == [job.external_id for job in jobs]


def test_public_bank_cannot_hide_parser_breakage_as_empty():
    with pytest.raises(RuntimeError, match="vacancy headings"):
        parse_public_bank("<main>Email: hrdho@publicbank.com.vn</main>", BOARD)
    with pytest.raises(RuntimeError, match="application email"):
        parse_public_bank(_fixture().replace("hrdho@publicbank.com.vn", "no-contact"), BOARD)
    with pytest.raises(RuntimeError, match="outside"):
        parse_public_bank(_fixture(), "https://fake-recruitment.example/jobs")


def test_public_bank_raises_on_http_error_and_accepts_empty_target_titles(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            return httpx.Response(200, text=_fixture(), request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    source = {"name": "Public Bank careers", "employer_name": "Public Bank Vietnam", "url": BOARD}
    assert len(asyncio.run(collect_public_bank(source))) == 2

    class BadClient(Client):
        async def get(self, url):
            return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", BadClient)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(collect_public_bank(source))
