import asyncio

import pytest
from playwright.async_api import async_playwright

from job_radar.collectors import _collect_linkedin_search_results


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
                    ("101", "AI Engineer", "Acme"), ("102", "Data Analyst", "Beta Bank")]
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
                  <button id="next" onclick="nextPage()">Next</button>
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
                          Research Engineer<br>Research Engineer<br>Beta<br>Hanoi<br>Posted 1 day ago
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
