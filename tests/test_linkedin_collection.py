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
                  <div role="button" tabindex="0" onclick="selectJob('101', 'AI Engineer', 'Design AI services with Python and deploy them to production.')">
                    AI Engineer<br>AI Engineer<br>Acme<br>Hanoi (Remote)<br>Posted 2 hours ago
                  </div>
                  <div role="button" tabindex="0" onclick="selectJob('102', 'Data Analyst', 'Analyze customer behavior and build dashboards using SQL.')">
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
