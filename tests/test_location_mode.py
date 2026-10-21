import json

from job_radar.db import now
from job_radar.ingest import ObservedJob, ingest
from job_radar.local_analysis import MatchJudgment, clean_saved_analysis
from job_radar.location_mode import normalize_saved_locations, split_location_work_mode
from job_radar.search_intent import fit_summary
from job_radar.settings import Settings
from job_radar.web import create_app


def test_split_source_location_and_work_mode() -> None:
    assert split_location_work_mode("Đống Đa (On-site)") == ("Đống Đa", "On-site")
    assert split_location_work_mode("Hanoi (Hybrid)") == ("Hanoi", "Hybrid")
    assert split_location_work_mode("Vietnam (Remote)") == ("Vietnam", "Remote")
    assert split_location_work_mode("District 1 (HCMC)") == ("District 1 (HCMC)", "")


def test_ingest_and_backfill_keep_place_and_work_mode_separate(tmp_path) -> None:
    db = create_app(Settings(tmp_path)).state.db
    source = "linkedin-test"
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'linkedin','Test','https://linkedin.com/jobs/test',?)",
               (source, now()))
    job_id, _ = ingest(db, source, ObservedJob(
        "https://linkedin.com/jobs/view/42", "Applied AI Engineer", "FiinGroup",
        "Build AI products in Đống Đa.", location="Đống Đa (On-site)"))
    row = db.one("SELECT location,work_mode FROM vacancies WHERE id=?", (job_id,))
    assert (row["location"], row["work_mode"]) == ("Đống Đa", "On-site")
    observation = db.one("SELECT id,payload FROM observations WHERE source_id=?", (source,))
    assert json.loads(observation["payload"])["work_mode"] == "On-site"

    # Recreate an older record, including the observation used by refresh_vacancy.
    payload = json.loads(observation["payload"])
    payload.update(location="Đống Đa (On-site)", work_mode="")
    db.execute("UPDATE observations SET payload=? WHERE id=?", (json.dumps(payload), observation["id"]))
    db.execute("UPDATE vacancies SET location='Đống Đa (On-site)',work_mode='' WHERE id=?", (job_id,))
    db.execute("DELETE FROM settings WHERE key='location_work_mode_v1_normalized'")
    assert normalize_saved_locations(db) == 2
    assert normalize_saved_locations(db) == 0
    row = db.one("SELECT location,work_mode FROM vacancies WHERE id=?", (job_id,))
    assert (row["location"], row["work_mode"]) == ("Đống Đa", "On-site")
    assert json.loads(db.one("SELECT payload FROM observations WHERE id=?", (observation["id"],))["payload"])["location"] == "Đống Đa"

    criteria = {name: {"score": 8, "reason": "Supported by the posting."}
                for name in MatchJudgment.model_fields if name != "summary"}
    detail = {"method": "local_llm", "facts": {"location": "", "work_mode": "", "years_required": None},
              "criteria": criteria}
    db.execute("UPDATE vacancies SET analysis_status='done',score_detail=? WHERE id=?", (json.dumps(detail), job_id))
    assert clean_saved_analysis(db) == 1
    row = db.one("SELECT score,score_detail FROM vacancies WHERE id=?", (job_id,))
    saved = json.loads(row["score_detail"])
    assert (saved["facts"]["location"], saved["facts"]["work_mode"]) == ("Đống Đa", "On-site")
    assert "location" not in fit_summary(row["score"], saved, {})["missing_evidence"]
    assert "work mode" not in fit_summary(row["score"], saved, {})["missing_evidence"]
