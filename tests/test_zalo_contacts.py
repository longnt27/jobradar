import json
from pathlib import Path

import pytest

from job_radar.db import Database, new_id, now
from job_radar.ingest import ObservedJob, ingest


def facebook_job(db, *, group="AI Jobs Vietnam", title="AI Engineer", text="Hiring AI Engineer. Zalo: 0912 345 678"):
    source = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'facebook',?,?,?)",
               (source, group, f"https://www.facebook.com/groups/{source}", now()))
    vacancy, _ = ingest(db, source, ObservedJob(f"https://www.facebook.com/groups/{source}/posts/1", title,
                                              "Example", text, raw_text=text))
    observation = db.one("SELECT id FROM observations WHERE source_id=?", (source,))["id"]
    return vacancy, observation


def test_default_message_uses_em_and_exact_cv_question():
    from job_radar.zalo_contacts import DEFAULT_ZALO_TEMPLATE, render_zalo_message
    assert render_zalo_message(DEFAULT_ZALO_TEMPLATE, "AI Engineer", "AI Jobs Vietnam") == (
        "Em chào anh/chị, em thấy bài đăng tuyển vị trí AI Engineer trong nhóm Facebook AI Jobs Vietnam.\n"
        "Anh/chị có thể chia sẻ JD chi tiết cho em được không ạ?\n"
        "Em có thể gửi CV qua Zalo cho anh/chị được không ạ?"
    )


def test_contacts_require_zalo_on_same_or_adjacent_line(tmp_path: Path):
    from job_radar.zalo_contacts import contact_candidates
    db = Database(tmp_path / "test.db")
    vacancy, _ = facebook_job(db, text="AI Engineer\nPhone: 0901 222 333\nSalary 200000000\nStart 12/10/2026")
    assert contact_candidates(db, vacancy) == []
    vacancy2, observation = facebook_job(db, text="AI Engineer\nContact via Zalo:\n0912.345.678\n0976 543 210")
    rows = contact_candidates(db, vacancy2)
    assert [(row["observation_id"], row["phone"]) for row in rows] == [(observation, "0912345678")]
    assert "Zalo" in rows[0]["evidence"]


@pytest.mark.parametrize("value,want", [("0912 345 678", "0912345678"), ("84912345678", "0912345678"),
                                          ("+84 912 345 678", "0912345678"), ("+1 (415) 555-2671", "+14155552671")])
def test_phone_formats_share_one_identity(value, want):
    from job_radar.zalo_contacts import normalize_zalo_phone
    assert normalize_zalo_phone(value) == want


@pytest.mark.parametrize("value", ["", "200000000", "12/10/2026", "123", "abc0912345678"])
def test_invalid_phone_is_rejected(value):
    from job_radar.zalo_contacts import normalize_zalo_phone
    with pytest.raises(ValueError):
        normalize_zalo_phone(value)


def test_multiple_sightings_keep_their_own_context(tmp_path: Path):
    from job_radar.zalo_contacts import contact_candidates
    db = Database(tmp_path / "test.db")
    vacancy, first = facebook_job(db)
    other, second = facebook_job(db, group="Vietnam Tech Jobs", title="Vision Engineer", text="AI Engineer Zalo 0976 543 210")
    db.execute("UPDATE vacancy_observations SET vacancy_id=? WHERE observation_id=?", (vacancy, second))
    rows = contact_candidates(db, vacancy)
    assert {(r["observation_id"], r["group"], r["position"], r["phone"]) for r in rows} == {
        (first, "AI Jobs Vietnam", "AI Engineer", "0912345678"),
        (second, "Vietnam Tech Jobs", "Vision Engineer", "0976543210"),
    }


def test_template_rejects_unknown_fields():
    from job_radar.zalo_contacts import validate_zalo_template, render_zalo_message
    for template in ("{email}", "{position", "{position.__class__}", "{position!r}", "{group:20}", "  "):
        with pytest.raises(ValueError):
            validate_zalo_template(template)
    assert render_zalo_message("{position} / {group}", "{email}", "<script>") == "{email} / <script>"
