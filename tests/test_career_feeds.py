from pathlib import Path

from job_radar.collectors import _expired_posting, _target_title
from job_radar.db import Database
from job_radar.employer_scope import HCMC_BASED
from job_radar.feed_catalog import CAREER_FEEDS
from job_radar.ranking import score_job
from job_radar.seeds import seed


def test_company_feeds_are_seeded_idempotently(tmp_path: Path) -> None:
    assert len(CAREER_FEEDS) == len({feed.employer for feed in CAREER_FEEDS}) >= 40
    assert {"VinDynamics", "VinRobotics", "VinMotion", "Techcombank", "Vietcombank", "MB Bank", "VPBank", "GPBank", "BIDV", "VNPT AI", "CMC Telecom"} <= {
        feed.employer for feed in CAREER_FEEDS
    }
    assert not {feed.employer for feed in CAREER_FEEDS} & HCMC_BASED.keys()
    assert all("linkedin.com" not in feed.url and "facebook.com" not in feed.url for feed in CAREER_FEEDS)

    db = Database(tmp_path / "jobs.db")
    seed(db)
    seed(db)
    rows = db.all("SELECT s.name,s.interval_minutes,s.config,e.name AS employer FROM sources s JOIN employers e ON e.id=s.employer_id WHERE s.kind='career'")
    assert len(rows) == len(CAREER_FEEDS)
    assert {row["employer"] for row in rows} == {feed.employer for feed in CAREER_FEEDS}
    assert all(row["interval_minutes"] == 240 and '"adapter"' in row["config"] for row in rows)


def test_expired_requisition_and_non_job_heading_are_rejected() -> None:
    assert _expired_posting("Senior AI Engineer Application deadline 14/11 — 14/11/2025")
    assert not _expired_posting("AI Engineer Application deadline 10/09 — 10/10/2099")
    assert not _expired_posting("Hạn nộp hồ sơ 28/09/2026 - 28/10/2099")
    assert _target_title("Senior Data Engineer")
    assert not _target_title("Careers")
    assert not _target_title("We build AI systems and seek an engineer. " * 10)


def test_company_publication_time_without_timezone_can_be_ranked() -> None:
    score, detail = score_job({
        "title": "AI Engineer", "description": "Build robot perception models.",
        "location": "Hanoi", "published_at": "2026-03-31T20:53:33",
    }, {})
    assert score > 0
    assert detail["components"]["freshness"] >= 0
