import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def _page(html: str):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    return playwright, browser, page


def test_career_form_opens_behind_non_apply_button() -> None:
    from job_radar.apply import _open_application_form

    async def run():
        playwright, browser, page = await _page('''
            <main><h1>Software Engineer</h1><button>Read job details</button>
            <button id="join">Join our team</button><section id="application" hidden>
            <form action="/submit" method="post" enctype="multipart/form-data">
            <label>Resume <input name="resume" type="file" required></label>
            <button type="submit">Send</button></form></section></main>
            <script>document.querySelector('#join').onclick = () => {
              document.querySelector('#application').hidden = false;
            };</script>''')
        try:
            opened, form = await _open_application_form(page)
            assert opened is page
            assert form["opener"]["label"] == "Join our team"
            assert form["fields"][0]["name"] == "resume"
            await page.reload()
            await page.set_content('''<main><h1>Software Engineer</h1><button>Read job details</button>
            <button id="join">Join our team</button><section id="application" hidden><form action="/submit" method="post" enctype="multipart/form-data">
            <label>Resume <input name="resume" type="file" required></label><button type="submit">Send</button></form></section></main>
            <script>document.querySelector('#join').onclick=()=>document.querySelector('#application').hidden=false;</script>''')
            _, reopened = await _open_application_form(page, form["opener"])
            assert reopened["signature"] == form["signature"]
        finally:
            await browser.close()
            await playwright.stop()

    asyncio.run(run())


def test_ambiguous_career_buttons_require_choice() -> None:
    from job_radar.apply import _open_application_form

    async def run():
        playwright, browser, page = await _page('''<main><h1>Designer</h1>
          <button>Join our team</button><button>Submit CV</button></main>''')
        try:
            try:
                await _open_application_form(page)
            except ValueError as error:
                assert "multiple" in str(error).lower()
                assert "Join our team" in str(error)
                assert "Submit CV" in str(error)
            else:
                raise AssertionError("Ambiguous actions must not be clicked")
        finally:
            await browser.close()
            await playwright.stop()

    asyncio.run(run())


def test_form_structure_filters_hidden_dummy_inputs() -> None:
    from job_radar.apply import _form_structure

    async def run():
        html = """
        <main>
          <form action="/apply" method="post" enctype="multipart/form-data">
            <label>First Name <input id="first_name" name="first_name" type="text" required></label>
            <input required="" tabindex="-1" aria-hidden="true" class="remix-css-1a0ro4n-requiredInput" style="opacity: 0" value="">
            <div style="display: none;">
              <input id="iti-search" type="search" placeholder="Search country">
            </div>
            <input type="text" style="display: none">
            <label>Resume <input id="resume" name="resume" type="file" required></label>
            <button type="submit">Submit application</button>
          </form>
        </main>
        """
        playwright, browser, page = await _page(html)
        try:
            form = await _form_structure(page)
            labels = [f["label"] for f in form["fields"]]
            ids = [f["id"] for f in form["fields"]]
            assert ids == ["first_name", "resume"]
            assert labels == ["First Name", "Resume"]
            assert form["fields"][0]["index"] == 0
            # resume is index 4 in form.locator("input,select,textarea")
            assert form["fields"][1]["index"] == 4
        finally:
            await browser.close()
            await playwright.stop()

    asyncio.run(run())


def test_signatures_compatible_normalizes_labels() -> None:
    from job_radar.apply import _signatures_compatible

    cur = [
        {"id": "name", "type": "text", "name": "", "label": "Name"},
        {"id": "cv", "type": "file", "name": "", "label": "Upload CV Here"},
    ]
    saved = [
        {"id": "name", "type": "text", "name": "", "label": "Name*"},
        {"id": "cv", "type": "file", "name": "", "label": "Upload CV\n\nHere"},
    ]
    # Identical structure with trailing asterisks or whitespace differences
    assert _signatures_compatible(cur, saved, "sig1", "sig2")

    # Mismatched ID should not be compatible
    cur_diff = [
        {"id": "other_id", "type": "text", "name": "", "label": "Name"},
        {"id": "cv", "type": "file", "name": "", "label": "Upload CV Here"},
    ]
    assert not _signatures_compatible(cur_diff, saved, "sig1", "sig2")

    # Mismatched question text should not be compatible
    cur_diff_q = [
        {"id": "name", "type": "text", "name": "", "label": "Your Phone Number"},
        {"id": "cv", "type": "file", "name": "", "label": "Upload CV Here"},
    ]
    assert not _signatures_compatible(cur_diff_q, saved, "sig1", "sig2")


def test_open_application_form_matches_opener_when_index_shifts() -> None:
    from job_radar.apply import _open_application_form

    async def run():
        # First page has button at index 0
        html1 = """
        <main>
          <button id="apply-btn">Apply now</button>
          <section id="form-sec" hidden>
            <form action="/submit" method="post">
              <label>Name <input name="name"></label>
              <button type="submit">Submit application</button>
            </form>
          </section>
        </main>
        <script>document.querySelector('#apply-btn').onclick = () => { document.querySelector('#form-sec').hidden = false; };</script>
        """
        # Second page has an extra link beforehand, shifting button index to 1
        html2 = """
        <main>
          <a href="/home">Home</a>
          <button id="apply-btn">Apply now</button>
          <section id="form-sec" hidden>
            <form action="/submit" method="post">
              <label>Name <input name="name"></label>
              <button type="submit">Submit application</button>
            </form>
          </section>
        </main>
        <script>document.querySelector('#apply-btn').onclick = () => { document.querySelector('#form-sec').hidden = false; };</script>
        """
        playwright, browser, page = await _page(html1)
        try:
            _, form1 = await _open_application_form(page)
            opener = form1["opener"]
            assert opener["label"] == "Apply now"

            # Now visit html2 where index has shifted
            await page.set_content(html2)
            _, form2 = await _open_application_form(page, reviewed_opener=opener)
            assert len(form2["fields"]) == 1
            assert form2["fields"][0]["name"] == "name"
        finally:
            await browser.close()
            await playwright.stop()

    asyncio.run(run())


def test_send_web_sanitizes_strict_phone_input(tmp_path: Path) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from fastapi.testclient import TestClient
    from job_radar.web import create_app
    from job_radar.settings import Settings
    from job_radar.ingest import ingest, ObservedJob
    from job_radar.drafting import prepare_draft, get_draft, update_draft
    from job_radar.db import new_id, now

    posted = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            html = """
            <html><body>
            <form action="/submit" method="post">
              <label for="name">Name</label>
              <input id="name" name="name" required>
              <label for="phone">Phone*</label>
              <input id="phone" type="tel" required>
              <button type="submit">Submit application</button>
            </form>
            <script>
              const phone = document.getElementById('phone');
              phone.addEventListener('input', (e) => {
                // Reject spaces/formatting: reset to empty if value contains spaces
                if (/\\s/.test(phone.value)) {
                  phone.value = '';
                }
              });
            </script>
            </body></html>
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())

        def do_POST(self):
            posted.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Thank you. Application submitted successfully</body></html>")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = Settings(tmp_path)
        client = TestClient(create_app(settings))
        db = client.app.state.db
        source_id = new_id()
        db.execute("INSERT INTO sources(id,kind,name,url,config,created_at) VALUES(?,?,?,?,?,?)",
                   (source_id, "career", "Career Co", "https://example.org", "{}", now()))
        job_url = f"http://127.0.0.1:{server.server_port}/jobs/1"
        vacancy_id, _ = ingest(db, source_id, ObservedJob(job_url, "AI Engineer", "Co", "Details", apply_url=job_url))
        profile = client.get("/api/profile").json()
        profile.update({"name": "Nguyen Trung Long", "email": "test@example.com", "phone": "0948 741 099"})
        client.put("/api/profile", json=profile)
        client.post("/api/positions", json={
            "company": "Prior Co", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built Python systems."]
        })
        draft = prepare_draft(db, settings, vacancy_id, "template")
        client.post(f"/api/applications/{draft['id']}/inspect")
        inspected = get_draft(db, draft["id"])

        # Verify inspected answer for phone has spaces from profile
        phone_field = next(f for f in inspected["form_data"]["fields"] if f["type"] == "tel")
        assert inspected["form_data"]["answers"][str(phone_field["index"])] == "0948 741 099"

        # Send application: _send_web should detect that the input cleared due to spaces,
        # sanitize it to digits-only (0948741099), and successfully submit!
        res = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": inspected["package_hash"]})
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "submitted_confirmed"
        assert len(posted) == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
