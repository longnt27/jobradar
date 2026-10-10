import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


EASY_APPLY_PAGE = """
<button id="apply">Easy Apply</button><button aria-label="Next" id="carousel"></button><div id="shell"></div>
<script>
  window.submitted = false;
  window.questionLabel = 'Years of machine learning experience?';
  window.reviewConsent = false;
  const root = document.querySelector('#shell').attachShadow({mode: 'open'});
  let step = 0;
  let uploaded = false;
  document.querySelector('#apply').onclick = () => { setTimeout(() => { step = 1; render(); }, 200); };
  root.addEventListener('click', event => {
    const action = event.target.dataset.action;
    if (action === 'upload') root.querySelector('input[type=file]').click();
    if (action === 'next') { step += 1; render(); }
    if (action === 'review' && root.querySelector('#years').value.trim()) { step = 4; render(); }
    if (action === 'submit') { window.submitted = true; root.innerHTML = '<p>Application sent</p>'; }
  });
  root.addEventListener('change', event => {
    if (event.target.type === 'file') uploaded = event.target.files.length > 0;
  });
  function render() {
    const content = step === 1
      ? '<label>Email address*<select required><option value="">Choose</option><option value="alex@example.org" selected>alex@example.org</option></select></label><label>Mobile phone number*<input type="tel" value="123456789" required></label>'
      : step === 2
      ? '<h3>Resume*</h3><input type="file" accept=".pdf" hidden><button data-action="upload">Upload resume</button><input type="radio" checked aria-label="Saved resume">'
      : step === 3
      ? `<label>${window.questionLabel}*<input id="years" type="text" required></label>`
      : `<h3>Review your application</h3>${window.reviewConsent ? '<label>I agree to contact*<input type="checkbox" required></label>' : ''}`;
    const action = step < 3 ? 'next' : step === 3 ? 'review' : 'submit';
    const label = action === 'next' ? 'Next' : action === 'review' ? 'Review' : 'Submit application';
    root.innerHTML = `<div class="modal"><div>${step}/4 pages</div><div>${content}</div><footer><button data-action="${action}">${label}</button></footer></div>`;
  }
</script>
"""


def test_inspection_collects_shadow_dialog_fields_and_never_submits(tmp_path: Path) -> None:
    from job_radar.linkedin_application import inspect_easy_apply_dialog

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\nexample")

    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(EASY_APPLY_PAGE)
                first = await inspect_easy_apply_dialog(page, "https://www.linkedin.com/jobs/view/123/", {}, resume)
                assert [field["label"] for field in first["fields"]] == [
                    "Email address", "Mobile phone number", "Resume", "Years of machine learning experience?",
                ]
                assert first["answers"]["0"] == "alex@example.org"
                assert first["answers"]["1"] == "123456789"
                assert first["attachments"]["2"] == {"kind": "resume"}
                assert not first["complete"]
                assert not await page.evaluate("window.submitted")

                await page.goto("about:blank")
                await page.set_content(EASY_APPLY_PAGE)
                second = await inspect_easy_apply_dialog(page, "https://www.linkedin.com/jobs/view/123/", {"3": "2"}, resume)
                assert second["complete"]
                assert second["answers"]["3"] == "2"
                assert second["signature"] == first["signature"]
                assert not await page.evaluate("window.submitted")
            finally:
                await browser.close()

    asyncio.run(run())


def test_review_page_questions_are_collected_before_submission(tmp_path: Path) -> None:
    from job_radar.linkedin_application import inspect_easy_apply_dialog

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\nexample")

    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(EASY_APPLY_PAGE)
                await page.evaluate("window.reviewConsent = true")
                first = await inspect_easy_apply_dialog(page, "https://www.linkedin.com/jobs/view/123/", {"3": "2"}, resume)
                assert first["fields"][-1]["label"] == "I agree to contact"
                assert not first["complete"]
                assert not await page.evaluate("window.submitted")
                await page.goto("about:blank")
                await page.set_content(EASY_APPLY_PAGE)
                await page.evaluate("window.reviewConsent = true")
                second = await inspect_easy_apply_dialog(page, "https://www.linkedin.com/jobs/view/123/", {"3": "2", "4": "yes"}, resume)
                assert second["complete"]
                assert not await page.evaluate("window.submitted")
            finally:
                await browser.close()

    asyncio.run(run())


def test_submission_fills_reviewed_answers_and_stops_on_changed_question(tmp_path: Path) -> None:
    from job_radar.linkedin_application import inspect_easy_apply_dialog, submit_easy_apply_dialog

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\nexample")

    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(EASY_APPLY_PAGE)
                form = await inspect_easy_apply_dialog(page, "https://www.linkedin.com/jobs/view/123/", {"3": "2"}, resume)
                assert form["complete"]
                await page.goto("about:blank")
                await page.set_content(EASY_APPLY_PAGE)
                status, _ = await submit_easy_apply_dialog(page, form, resume)
                assert status == "submitted_confirmed"
                assert await page.evaluate("window.submitted")

                await page.goto("about:blank")
                await page.set_content(EASY_APPLY_PAGE)
                await page.evaluate("window.questionLabel = 'Years of deep learning experience?'")
                status, _ = await submit_easy_apply_dialog(page, form, resume)
                assert status == "needs_user_attention"
                assert not await page.evaluate("window.submitted")
            finally:
                await browser.close()

    asyncio.run(run())


def test_linkedin_apply_control_waits_for_page_hydration() -> None:
    from job_radar.linkedin_application import wait_for_linkedin_apply_control

    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('''<main><h1>Engineer</h1><h2>About the job</h2></main><script>
                  setTimeout(() => { document.querySelector('main h2').insertAdjacentHTML(
                    'beforebegin', '<button>Easy Apply</button>'); }, 250);
                </script>''')
                action = await wait_for_linkedin_apply_control(page)
                assert action["kind"] == "linkedin_easy_apply"
            finally:
                await browser.close()

    asyncio.run(run())


def test_single_page_easy_apply_without_progress_counter(tmp_path: Path) -> None:
    from job_radar.linkedin_application import inspect_easy_apply_dialog

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\nexample")

    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('''<button id="apply">Easy Apply</button><div id="host"></div><script>
                  window.submitted=false;
                  document.querySelector('#apply').onclick=()=>{
                    document.querySelector('#host').innerHTML=`<div role="dialog"><h2>Apply to Example</h2>
                      <label>Email*<select required><option value="a@example.org" selected>a@example.org</option></select></label>
                      <label>Phone*<input type="tel" value="123456789" required></label>
                      <h3>Resume</h3><button type="button" id="upload">Upload resume</button><input type="file" hidden>
                      <button id="submit">Submit application</button></div>`;
                    document.querySelector('#upload').onclick=()=>document.querySelector('input[type=file]').click();
                    document.querySelector('#submit').onclick=()=>window.submitted=true;
                  };
                </script>''')
                form = await inspect_easy_apply_dialog(page, "https://www.linkedin.com/jobs/view/123/", {}, resume)
                assert form["complete"]
                assert form["steps_total"] == 1
                assert [field["label"] for field in form["fields"]] == ["Email", "Phone", "Resume"]
                assert not await page.evaluate("window.submitted")
            finally:
                await browser.close()

    asyncio.run(run())


def test_related_job_apply_is_not_used_for_selected_posting() -> None:
    from job_radar.linkedin_application import find_linkedin_apply_control

    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('''<main><article><h1>Old job</h1><p>No longer accepting applications</p>
                  <h2>About the job</h2></article><aside><h2>More jobs</h2><button>Easy Apply</button></aside></main>''')
                action = await find_linkedin_apply_control(page)
                assert action["kind"] == "closed"
                await page.set_content('''<main><article><h1>Closed job</h1><p>Not currently accepting applications</p>
                  <h2>About the job</h2></article></main>''')
                action = await find_linkedin_apply_control(page)
                assert action["kind"] == "closed"
                await page.set_content('''<main><article><h1>Vietnamese Closed job</h1><p>Hiện không nhận đơn</p>
                  <h2>About the job</h2></article></main>''')
                action = await find_linkedin_apply_control(page)
                assert action["kind"] == "closed"
                await page.set_content('''<main><article><h1>Already sent</h1><p>Applied on company site</p>
                  <h2>About the job</h2></article><aside><button>Easy Apply</button></aside></main>''')
                action = await find_linkedin_apply_control(page)
                assert action["kind"] == "already_applied"
            finally:
                await browser.close()

    asyncio.run(run())


def test_easy_apply_with_radiogroup_questions_and_review_step(tmp_path: Path) -> None:
    from job_radar.linkedin_application import inspect_easy_apply_dialog, submit_easy_apply_dialog

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\nexample")

    page_html = """
    <button id="apply">Easy Apply</button><div id="host"></div>
    <script>
      window.submitted = false;
      let step = 0;
      document.querySelector('#apply').onclick = () => { step = 1; render(); };
      function render() {
        const host = document.querySelector('#host');
        if (step === 1) {
          host.innerHTML = `<dialog open data-testid="dialog"><div>1/4 pages</div>
            <label>Email address*<select required><option value="a@example.com" selected>a@example.com</option></select></label>
            <label>Phone number*<input type="tel" value="123456" required></label>
            <footer><button id="next1">Next</button></footer></dialog>`;
          host.querySelector('#next1').onclick = () => { step = 2; render(); };
        } else if (step === 2) {
          host.innerHTML = `<dialog open data-testid="dialog"><div>2/4 pages</div>
            <h3>Resume</h3><input type="file" accept=".pdf" hidden><button id="upload">Upload resume</button>
            <input type="radio" checked aria-label="Saved resume">
            <footer><button id="next2">Next</button></footer></dialog>`;
          host.querySelector('#next2').onclick = () => { step = 3; render(); };
        } else if (step === 3) {
          host.innerHTML = `<dialog open data-testid="dialog"><div>3/4 pages</div>
            <fieldset role="radiogroup">
              <p>Are you willing to take a drug test, in accordance with local law/regulations?*</p>
              <div role="radio"><input type="radio" id="r1" name="drug_test" value="Yes"><label for="r1"></label><p>Yes</p></div>
              <div role="radio"><input type="radio" id="r2" name="drug_test" value="No"><label for="r2"></label><p>No</p></div>
            </fieldset>
            <footer><button id="review-btn">Review</button></footer></dialog>`;
          host.querySelectorAll('[role="radio"]').forEach(div => {
            div.onclick = () => { div.querySelector('input').checked = true; };
          });
          host.querySelector('#review-btn').onclick = () => {
            if (host.querySelector('#r1').checked || host.querySelector('#r2').checked) {
              step = 4;
              render();
            }
          };
        } else if (step === 4) {
          host.innerHTML = `<dialog open data-testid="dialog"><div>4/4 pages</div>
            <h3>Review your application</h3>
            <footer><button id="submit-btn">Submit application</button></footer></dialog>`;
          host.querySelector('#submit-btn').onclick = () => {
            window.submitted = true;
            host.innerHTML = '<p>Application submitted</p>';
          };
        }
      }
    </script>
    """

    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(page_html)
                form = await inspect_easy_apply_dialog(page, "https://www.linkedin.com/jobs/view/4475150267/", {}, resume)
                assert form["complete"]
                assert form["steps_total"] == 4
                radio_fields = [f for f in form["fields"] if f["type"] == "radio"]
                assert len(radio_fields) == 1
                drug_field = radio_fields[0]
                assert "drug test" in drug_field["label"].casefold()
                assert len(drug_field["options"]) == 2
                assert [o["value"] for o in drug_field["options"]] == ["Yes", "No"]
                assert form["answers"][str(drug_field["index"])] == "Yes"
                assert not await page.evaluate("window.submitted")

                # Now test submission traverses all 4 steps and submits
                await page.goto("about:blank")
                await page.set_content(page_html)
                status, receipt = await submit_easy_apply_dialog(page, form, resume)
                assert status == "submitted_confirmed"
                assert await page.evaluate("window.submitted")
            finally:
                await browser.close()

    asyncio.run(run())

