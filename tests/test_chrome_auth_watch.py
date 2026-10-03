import sqlite3
import time

from job_radar.chrome_auth_watch import CHROME_EPOCH_OFFSET, chrome_login_complete


def test_detects_only_a_new_final_account_visit(tmp_path):
    history = tmp_path / "Default" / "History"
    history.parent.mkdir()
    with sqlite3.connect(history) as conn:
        conn.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT);"
                           "CREATE TABLE visits(url INTEGER, visit_time INTEGER);")
        started = time.time()
        base = int((started + CHROME_EPOCH_OFFSET) * 1_000_000)
        conn.execute("INSERT INTO urls VALUES(1, 'https://www.linkedin.com/feed/')")
        conn.execute("INSERT INTO urls VALUES(2, 'https://www.linkedin.com/login')")
        conn.execute("INSERT INTO visits VALUES(1, ?)", (base - 1_000_000,))
    assert not chrome_login_complete(tmp_path, "linkedin", started)
    with sqlite3.connect(history) as conn:
        conn.execute("INSERT INTO visits VALUES(2, ?)", (base + 1_000_000,))
    assert not chrome_login_complete(tmp_path, "linkedin", started)
    with sqlite3.connect(history) as conn:
        conn.execute("INSERT INTO visits VALUES(1, ?)", (base + 2_000_000,))
    assert chrome_login_complete(tmp_path, "linkedin", started)
    assert not chrome_login_complete(tmp_path, "facebook", started)


def test_facebook_requires_settings_page_on_real_domain(tmp_path):
    history = tmp_path / "Default" / "History"
    history.parent.mkdir()
    with sqlite3.connect(history) as conn:
        conn.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT);"
                           "CREATE TABLE visits(url INTEGER, visit_time INTEGER);")
        started = time.time()
        at = int((started + CHROME_EPOCH_OFFSET) * 1_000_000) + 1_000_000
        conn.execute("INSERT INTO urls VALUES(1, 'https://www.facebook.com/settings.evil.test/')")
        conn.execute("INSERT INTO urls VALUES(2, 'https://www.facebook.com/settings')")
        conn.execute("INSERT INTO visits VALUES(1, ?)", (at,))
    assert not chrome_login_complete(tmp_path, "facebook", started)
    with sqlite3.connect(history) as conn:
        conn.execute("INSERT INTO visits VALUES(2, ?)", (at + 1,))
    assert chrome_login_complete(tmp_path, "facebook", started)
