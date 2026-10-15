import json

from job_radar.db import Database, new_id, now
from job_radar.ingest import ObservedJob, ingest


def test_llm_extracted_identity_updates_facebook_job_and_search_index(tmp_path):
    from job_radar.local_analysis import save_extracted_job_identity
    db = Database(tmp_path / "jobs.sqlite3")
    source = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'facebook','Group','https://facebook.com/groups/test',?)", (source, now()))
    text = "[Saramin's Client/HN] AI NATIVE SOFTWARE ENGINEER ơi! WEEDS VINA mở DEAL XỊN - APPLY ngay!"
    job_id, _ = ingest(db, source, ObservedJob("https://facebook.com/groups/test/posts/1", text, "Facebook post", text))
    with db.connection() as conn:
        save_extracted_job_identity(conn, job_id, {"role": "AI Native Software Engineer", "company": "WEEDS VINA"})
    assert db.one("SELECT title,company FROM vacancies WHERE id=?", (job_id,)) == {"title": "AI Native Software Engineer", "company": "WEEDS VINA"}
    assert db.one("SELECT title,company FROM vacancy_fts WHERE vacancy_id=?", (job_id,)) == {"title": "AI Native Software Engineer", "company": "WEEDS VINA"}
    row = db.one("SELECT raw_text,payload FROM observations WHERE source_id=?", (source,))
    assert row["raw_text"] == text
    assert json.loads(row["payload"])["title"] == "AI Native Software Engineer"


def test_unstated_model_identity_does_not_overwrite_job(tmp_path):
    from job_radar.local_analysis import save_extracted_job_identity
    db = Database(tmp_path / "jobs.sqlite3")
    source = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'facebook','Group','https://facebook.com/groups/test',?)", (source, now()))
    job_id, _ = ingest(db, source, ObservedJob("https://facebook.com/groups/test/posts/1", "AI Engineer", "Facebook post", "Hiring an AI Engineer, Python experience required."))
    with db.connection() as conn:
        save_extracted_job_identity(conn, job_id, {"role": "Data Scientist", "company": "Imaginary Corp"})
    assert db.one("SELECT title,company FROM vacancies WHERE id=?", (job_id,)) == {"title": "AI Engineer", "company": "Facebook post"}


def test_location_prefix_is_not_saved_as_the_hiring_employer(tmp_path):
    from job_radar.local_analysis import save_extracted_job_identity
    db = Database(tmp_path / "jobs.sqlite3")
    source = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'facebook','Group','https://facebook.com/groups/test',?)", (source, now()))
    text = "[Ngoại Giao Đoàn] Cần gấp nhân sự cho vị trí: AI Engineer (Max 30M)"
    job_id, _ = ingest(db, source, ObservedJob("https://facebook.com/groups/test/posts/2", text, "Facebook post", text))
    with db.connection() as conn:
        save_extracted_job_identity(conn, job_id, {"role": "AI Engineer", "company": "Ngoại Giao Đoàn"})
    assert db.one("SELECT company FROM vacancies WHERE id=?", (job_id,))["company"] == "Facebook post"


def test_llm_identity_survives_unchanged_rescan_but_expires_when_source_changes(tmp_path):
    from job_radar.local_analysis import save_extracted_job_identity
    db = Database(tmp_path / "jobs.sqlite3")
    source = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'facebook','Group','https://facebook.com/groups/test',?)", (source, now()))
    text = "WEEDS VINA tuyển AI NATIVE SOFTWARE ENGINEER, APPLY ngay!"
    observed = ObservedJob("https://facebook.com/groups/test/posts/3", text, "Facebook post", text)
    job_id, _ = ingest(db, source, observed)
    with db.connection() as conn:
        save_extracted_job_identity(conn, job_id, {"role": "AI Native Software Engineer", "company": "WEEDS VINA"})
    db.execute("UPDATE vacancies SET analysis_status='done' WHERE id=?", (job_id,))
    ingest(db, source, observed)
    assert db.one("SELECT title,company,analysis_status FROM vacancies WHERE id=?", (job_id,)) == {
        "title": "AI Native Software Engineer", "company": "WEEDS VINA", "analysis_status": "done"}
    observed.title = observed.description = "WEEDS VINA tuyển Product Manager"
    ingest(db, source, observed)
    assert db.one("SELECT title,analysis_status FROM vacancies WHERE id=?", (job_id,)) == {
        "title": "WEEDS VINA tuyển Product Manager", "analysis_status": "not_configured"}


def test_model_can_expand_grounded_role_abbreviation(tmp_path):
    from job_radar.local_analysis import save_extracted_job_identity
    db = Database(tmp_path / "jobs.sqlite3")
    source = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'facebook','Group','https://facebook.com/groups/test',?)", (source, now()))
    text = "PANCAKE TUYỂN DỤNG BA"
    job_id, _ = ingest(db, source, ObservedJob("https://facebook.com/groups/test/posts/4", text, "Facebook post", text))
    with db.connection() as conn:
        save_extracted_job_identity(conn, job_id, {"role": "Business Analyst", "company": "PANCAKE"})
    assert db.one("SELECT title,company FROM vacancies WHERE id=?", (job_id,)) == {"title": "Business Analyst", "company": "PANCAKE"}


def test_extracted_position_cleans_native_headline_without_changing_known_employer(tmp_path):
    from job_radar.local_analysis import save_extracted_job_identity
    db = Database(tmp_path / "jobs.sqlite3")
    source = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'linkedin','Search','https://linkedin.com/jobs',?)", (source, now()))
    observed = ObservedJob("https://linkedin.com/jobs/view/123", "[HN] AI Engineer | Salary up to 50M", "Known Employer", "Build Python models and APIs.")
    job_id, _ = ingest(db, source, observed)
    with db.connection() as conn:
        save_extracted_job_identity(conn, job_id, {"role": "AI Engineer", "company": "Python"})
    ingest(db, source, observed)
    assert db.one("SELECT title,company FROM vacancies WHERE id=?", (job_id,)) == {"title": "AI Engineer", "company": "Known Employer"}
    observed.title = "Data Engineer"
    observed.company = "Updated Employer"
    ingest(db, source, observed)
    assert db.one("SELECT title,company FROM vacancies WHERE id=?", (job_id,)) == {"title": "Data Engineer", "company": "Updated Employer"}


def test_social_identity_keeps_stated_specialization_when_model_adds_engineer(tmp_path):
    from job_radar.local_analysis import save_extracted_job_identity
    db = Database(tmp_path / "jobs.sqlite3")
    source = new_id()
    db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,'facebook','Group','https://facebook.com/groups/test',?)", (source, now()))
    text = "WE’RE HIRING | COMPUTER VISION\nHà Nội | Onsite\nAI & Robotics\nCOMPUTER VISION\nTừ 1 năm kinh nghiệm AI/Computer Vision"
    job_id, _ = ingest(db, source, ObservedJob("https://facebook.com/groups/test/posts/5", text, "Facebook post", text))
    with db.connection() as conn:
        save_extracted_job_identity(conn, job_id, {"role": "Computer Vision Engineer", "company": "WE’RE HIRING | COMPUTER VISION"})
    assert db.one("SELECT title,company FROM vacancies WHERE id=?", (job_id,)) == {
        "title": "Computer Vision", "company": "Facebook post"}
