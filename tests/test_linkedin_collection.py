import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_radar.collectors import AccountWarning, AuthRequired, _check_auth, _collect_linkedin_search_results
from job_radar.settings import Settings
from job_radar.web import create_app


def test_linkedin_join_page_requires_sign_in_instead_of_becoming_a_job() -> None:
    join_page = (
        "Skip to main content LinkedIn Join LinkedIn Email Password (6+ characters) "
        "By clicking Agree & Join, you agree to the LinkedIn User Agreement."
    )
    with pytest.raises(AuthRequired, match="Login or verification"):
        _check_auth("https://www.linkedin.com/jobs/view/123/", join_page)
    _check_auth("https://www.linkedin.com/jobs/view/123/", "AI Engineer at Acme. Build AI services with Python.")
    with pytest.raises(AccountWarning):
        _check_auth("https://www.linkedin.com/checkpoint/", "We noticed some unusual activity on your account. Your account has accessed a high volume of LinkedIn profile data.")


def test_account_warning_stops_future_linkedin_scans(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    source_id = app.state.db.one("SELECT id FROM sources WHERE kind='linkedin' LIMIT 1")["id"]

    async def warning(_settings, _source):
        raise AccountWarning("LinkedIn showed an account activity warning")

    monkeypatch.setattr("job_radar.scanner.collect_source", warning)
    monkeypatch.setattr("job_radar.scanner.notify_social_sign_in_required", lambda _site: None)
    result = asyncio.run(app.state.scan_manager.run_source(source_id))
    assert result["status"] == "auth_required"
    assert app.state.db.get_setting("linkedin_automation_paused", False)
    assert asyncio.run(app.state.scan_manager.run_source(source_id))["status"] == "paused"


def test_new_linkedin_results_page_reads_card_details() -> None:
    async def check() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("""
                  <div role="button" tabindex="0" componentkey="job-card-component-ref-101" onclick="selectJob('101', 'AI Engineer', 'Design AI services with Python and deploy them to production.')">
                    AI Engineer<br>AI Engineer<br>Acme<br>Hanoi (Remote)<br>Posted 2 hours ago
                  </div>
                  <div role="button" tabindex="0" componentkey="job-card-component-ref-102" onclick="selectJob('102', 'Data Analyst', 'Analyze customer behavior and build dashboards using SQL.')">
                    Data Analyst<br>Data Analyst<br>Beta Bank<br>Hanoi (On-site)<br>Posted 1 day ago
                  </div>
                  <a id="job-link" href="https://www.linkedin.com/jobs/view/101/">AI Engineer</a>
                  <section><div><h2>About the job</h2></div><p id="description">Design AI services with Python and deploy them to production.</p></section>
                  <script>
                    function selectJob(id, title, description) {
                      const link = document.getElementById('job-link');
                      link.href = `https://www.linkedin.com/jobs/view/${id}/`;
                      link.textContent = title;
                      document.getElementById('description').textContent = description;
                    }
                  </script>
                """)
                jobs = await _collect_linkedin_search_results(page, {"config": {"max_results": 10}})
                assert [(job.external_id, job.title, job.company) for job in jobs] == [
                    ("101", "AI Engineer", "Acme")]
                assert all(len(job.description) >= 30 for job in jobs)
                await page.set_content("<p>Unexpected LinkedIn screen</p>")
                with pytest.raises(RuntimeError, match="job cards were not recognized"):
                    await _collect_linkedin_search_results(page, {"config": {}})
            finally:
                await browser.close()

    asyncio.run(check())


def test_linkedin_results_continue_to_next_page() -> None:
    async def check() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("""
                  <div id="cards">
                    <div role="button" tabindex="0" componentkey="job-card-component-ref-101"
                      onclick="choose('101', 'AI Engineer', 'Acme', 'Build production AI systems using Python and machine learning.')">
                      AI Engineer<br>AI Engineer<br>Acme<br>Hanoi<br>Posted 2 hours ago
                    </div>
                  </div>
                  <button aria-label="Next" data-testid="carousel-inline-right-button">Next</button>
                  <button id="next" data-testid="pagination-controls-next-button-visible" onclick="nextPage()">Next</button>
                  <a id="job-link" href="https://www.linkedin.com/jobs/view/101/">AI Engineer</a>
                  <section><div><h2>About the job</h2></div><p id="description">Build production AI systems using Python and machine learning.</p></section>
                  <script>
                    function choose(id, title, company, description) {
                      const link = document.getElementById('job-link');
                      link.href = `https://www.linkedin.com/jobs/view/${id}/`;
                      link.textContent = title;
                      document.getElementById('description').textContent = description;
                    }
                    function nextPage() {
                      document.getElementById('cards').innerHTML = `
                        <div role="button" tabindex="0" componentkey="job-card-component-ref-202"
                          onclick="choose('202', 'Research Engineer', 'Beta', 'Research and deploy computer vision models for production robotics.')">
                          Research Engineer<br>Research Engineer<br>Beta<br>Hanoi<br>Posted 3 hours ago
                        </div>`;
                      document.getElementById('next').remove();
                    }
                  </script>
                """)
                jobs = await _collect_linkedin_search_results(page, {"config": {"max_results": 10}})
                assert [job.external_id for job in jobs] == ["101", "202"]
            finally:
                await browser.close()

    asyncio.run(check())



def test_linkedin_results_preserve_easy_apply_action() -> None:
    async def check() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("""
                  <main>
                    <div role="button" tabindex="0" componentkey="job-card-component-ref-303">
                      AI Engineer<br>AI Engineer<br>Acme<br>Hanoi<br>Posted 1 hour ago
                    </div>
                    <a href="https://www.linkedin.com/jobs/view/303/">AI Engineer</a>
                    <button class="jobs-apply-button" aria-label="Easy Apply">Easy Apply</button>
                    <section><div><h2>About the job</h2></div><p>Build production AI systems using Python and machine learning.</p></section>
                  </main>
                """)
                jobs = await _collect_linkedin_search_results(page, {"config": {"max_results": 1}})
                assert len(jobs) == 1
                assert jobs[0].apply_url == "https://www.linkedin.com/jobs/view/303/"
                assert "LinkedIn Easy Apply" in jobs[0].raw_text
            finally:
                await browser.close()

    asyncio.run(check())
