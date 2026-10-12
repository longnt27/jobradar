from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from job_radar.db import Database
from job_radar.job_inbox import enrich_jobs
from test_zalo_contacts import facebook_job


@pytest.fixture
def environment(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    job, observation = facebook_job(db)
    return db, job, observation


def ready_inquiry(db, job, observation):
    from job_radar.zalo_inquiry import create_inquiry, record_recipient
    draft = create_inquiry(db, job, observation)
    return record_recipient(db, draft["id"], {"phone": "0912345678", "display_name": "Recruiter", "identity_key": "user-123", "evidence": "Unique phone search"})


def test_inquiry_does_not_require_profile_resume_or_provider(environment):
    from job_radar.zalo_inquiry import create_inquiry
    db, job, observation = environment
    draft = create_inquiry(db, job, observation)
    assert "vị trí AI Engineer trong nhóm Facebook AI Jobs Vietnam" in draft["message"]
    assert draft["recipient"] == {}
    assert not draft["send_ready"]
    assert len(draft["package_hash"]) == 64


def test_edit_invalidates_old_review_hash(environment):
    from job_radar.zalo_inquiry import InquiryConflict, claim_inquiry_send, update_inquiry
    db, job, observation = environment
    draft = ready_inquiry(db, job, observation)
    assert draft["send_ready"]
    edited = update_inquiry(db, draft["id"], {"message": "Em xin JD chi tiết ạ."})
    assert edited["package_hash"] != draft["package_hash"]
    with pytest.raises(InquiryConflict):
        claim_inquiry_send(db, draft["id"], draft["package_hash"])
    edited = update_inquiry(db, draft["id"], {"phone": "0976543210"})
    assert edited["recipient"] == {}
    assert not edited["send_ready"]


def test_template_setting_does_not_replace_existing_draft(environment):
    from job_radar.zalo_inquiry import create_inquiry, get_inquiry, update_inquiry, regenerate_inquiry
    db, job, observation = environment
    draft = create_inquiry(db, job, observation)
    update_inquiry(db, draft["id"], {"position": "Correct position", "message": "Em viết riêng ạ."})
    db.set_setting("zalo_inquiry_template", "Em hỏi về {position} ở {group}.")
    assert get_inquiry(db, draft["id"])["message"] == "Em viết riêng ạ."
    rebuilt = regenerate_inquiry(db, draft["id"])
    assert "vị trí Correct position" in rebuilt["message"]
    new = create_inquiry(db, job, observation)
    assert new["message"] == "Em hỏi về AI Engineer ở AI Jobs Vietnam."


def test_duplicate_approval_claim_is_atomic(environment):
    from job_radar.zalo_inquiry import InquiryConflict, claim_inquiry_send
    db, job, observation = environment
    first = ready_inquiry(db, job, observation)
    second = ready_inquiry(db, job, observation)
    def claim(draft):
        try:
            return claim_inquiry_send(db, draft["id"], draft["package_hash"])["status"]
        except InquiryConflict:
            return "blocked"
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(claim, (first, second))) == ["blocked", "sending"]
    assert db.one("SELECT COUNT(*) AS n FROM zalo_inquiry_attempts")["n"] == 1


def test_restart_preserves_uncertain_send_guard(environment):
    from job_radar.zalo_inquiry import claim_inquiry_send, recover_inquiry_sends, get_inquiry
    db, job, observation = environment
    draft = ready_inquiry(db, job, observation)
    claim_inquiry_send(db, draft["id"], draft["package_hash"])
    Database(db.path)
    assert db.one("SELECT status FROM zalo_inquiry_attempts")["status"] == "sending"
    assert recover_inquiry_sends(db) == 1
    assert db.one("SELECT status FROM zalo_inquiry_attempts")["status"] == "send_uncertain"
    assert not get_inquiry(db, draft["id"])["send_ready"]


def test_manual_resolution_requires_a_fresh_review_for_retry(environment):
    from job_radar.zalo_inquiry import claim_inquiry_send, finish_inquiry_send, resolve_inquiry_attempt, get_inquiry
    db, job, observation = environment
    draft = ready_inquiry(db, job, observation)
    attempt = claim_inquiry_send(db, draft["id"], draft["package_hash"])
    with pytest.raises(ValueError):
        resolve_inquiry_attempt(db, attempt["id"], False)
    finish_inquiry_send(db, attempt["id"], {"status": "send_uncertain", "error": "No confirmation"})
    assert resolve_inquiry_attempt(db, attempt["id"], False)["status"] == "confirmed_not_sent"
    retry = get_inquiry(db, draft["id"])
    assert not retry["send_ready"]
    assert retry["recipient"] == {}
    with pytest.raises(ValueError):
        claim_inquiry_send(db, retry["id"], retry["package_hash"])


def test_contact_does_not_change_application_progress(environment):
    from job_radar.zalo_inquiry import claim_inquiry_send, finish_inquiry_send, inquiry_context
    db, job, observation = environment
    before = enrich_jobs(db, [db.one("SELECT * FROM vacancies WHERE id=?", (job,))])[0]
    draft = ready_inquiry(db, job, observation)
    attempt = claim_inquiry_send(db, draft["id"], draft["package_hash"])
    finish_inquiry_send(db, attempt["id"], {"status": "sent_confirmed", "receipt": {"visible_status": "Đã gửi"}})
    after = enrich_jobs(db, [db.one("SELECT * FROM vacancies WHERE id=?", (job,))])[0]
    assert after["application_progress"] == before["application_progress"] == "not_started"
    assert db.one("SELECT COUNT(*) AS n FROM submissions")["n"] == 0
    assert inquiry_context(db, job)["contact_status"] == "awaiting_reply"


def test_observation_ownership_and_empty_position_are_rejected(environment):
    from job_radar.zalo_inquiry import create_inquiry, update_inquiry, record_recipient
    db, job, observation = environment
    other, unrelated = facebook_job(db, title="Other Role", text="Other job Zalo 0976543210")
    with pytest.raises(KeyError):
        create_inquiry(db, job, unrelated)
    draft = create_inquiry(db, job, observation)
    with pytest.raises(ValueError):
        update_inquiry(db, draft["id"], {"position": ""})
    with pytest.raises(ValueError):
        record_recipient(db, draft["id"], {"phone": "0976543210", "identity_key": "wrong", "display_name": "Someone"})
