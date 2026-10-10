from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import pytest

from fastapi.testclient import TestClient

from job_radar.apply import inspect_form, send_readiness
from job_radar.drafting import prepare_draft
from job_radar.web import create_app
from job_radar.settings import Settings


def test_lever_custom_question_cards_extraction_and_defaults(tmp_path: Path) -> None:
    html = b"""<!DOCTYPE html>
    <html>
    <body>
    <form action="/apply" method="post">
      <div class="section">
        <ul>
          <li class="application-question">
            <div class="application-label">Which location are you applying for?</div>
            <div class="application-field">
              <select name="opportunityLocationId">
                <option value="">Select...</option>
                <option value="loc-asia">Asia</option>
                <option value="loc-hk">Hong Kong</option>
              </select>
            </div>
          </li>
          <li class="application-question custom-question">
            <div class="application-label"><div class="text">Where do you currently live?<span class="required">&#10033;</span></div></div>
            <div class="application-field">
              <select name="cards[abc-123][field0]" required>
                <option value="">Select...</option>
                <option value="Vietnam">Vietnam</option>
                <option value="Singapore">Singapore</option>
              </select>
            </div>
          </li>
          <li class="application-question custom-question">
            <div class="application-label"><div class="text">Are you committed to working full-time (40 hours/5 days per week)?*<span class="required">&#10033;</span></div></div>
            <div class="application-field">
              <ul>
                <li><label><input type="radio" name="cards[abc-123][field1]" value="Yes" required><span>Yes</span></label></li>
                <li><label><input type="radio" name="cards[abc-123][field1]" value="No" required><span>No</span></label></li>
              </ul>
            </div>
          </li>
          <li class="application-question custom-question">
            <div class="application-label"><div class="text">If less than 40 hours/week, please state how many hours you are eligible to work per week*<span class="required">&#10033;</span></div></div>
            <div class="application-field">
              <textarea name="cards[abc-123][field2]" required></textarea>
            </div>
          </li>
          <li class="application-question custom-question">
            <div class="application-label"><div class="text">How did you hear about our job opening?<span class="required">&#10033;</span></div></div>
            <div class="application-field">
              <ul>
                <li><label><input type="radio" name="cards[abc-123][field3]" value="LinkedIn job posting" required><span>LinkedIn job posting</span></label></li>
                <li><label><input type="radio" name="cards[abc-123][field3]" value="Other" required><span>Other</span></label></li>
              </ul>
            </div>
          </li>
        </ul>
        <button type="submit">Submit application</button>
      </div>
    </form>
    </body>
    </html>
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        settings = Settings(tmp_path)
        app = create_app(settings)
        client = TestClient(app)

        profile = client.get("/api/profile").json()
        profile.update({"name": "Nguyen Trung Long", "email": "long@example.com", "country": "Vietnam", "location": "Hanoi, Vietnam"})
        client.put("/api/profile", json=profile)
        client.post("/api/positions", json={"company": "Tech Labs", "role": "AI Engineer", "dates": "2024 – 2026", "bullets": ["Built AI agents."]})
        job = client.post("/api/jobs/import", json={"company": "Binance", "title": "AI Agent Engineer", "description": "Build AI agents.", "apply_url": f"http://127.0.0.1:{server.server_port}/jobs/1"}).json()
        draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")

        # Set destination URL to the test form
        dest = {"kind": "web", "url": f"http://127.0.0.1:{server.server_port}/apply", "action_type": "web_form"}
        client.patch(f"/api/applications/{draft['id']}", json={"destination": dest})

        # Inspect form
        inspected = client.post(f"/api/applications/{draft['id']}/inspect").json()
        form_data = inspected["form_data"]
        fields = form_data["fields"]

        # Ensure no cryptic cards[...] labels exist
        for f in fields:
            assert not f["label"].startswith("cards["), f"Field label should not start with cards[: {f['label']}"
            assert f["label"] != "", f"Field label should not be empty: {f}"

        # Check location select
        loc_f = next(f for f in fields if f["name"] == "opportunityLocationId")
        assert "location" in loc_f["label"].lower()
        assert loc_f["group_label"] == "Which location are you applying for?"

        # Check country select
        country_f = next(f for f in fields if f["name"] == "cards[abc-123][field0]")
        assert country_f["label"] == "Where do you currently live?"
        assert form_data["answers"][str(country_f["index"])] == "Vietnam"

        # Check full-time radio group
        yes_f = next(f for f in fields if f["name"] == "cards[abc-123][field1]" and f["label"] == "Yes")
        no_f = next(f for f in fields if f["name"] == "cards[abc-123][field1]" and f["label"] == "No")
        assert "committed to working full-time" in yes_f["group_label"].lower()
        assert yes_f["group_label"] == no_f["group_label"]
        # Pre-filled Yes for eligibility
        assert form_data["answers"][str(yes_f["index"])] == "yes"
        assert form_data["answers"][str(no_f["index"])] == ""

        # Check textarea for hours
        hours_f = next(f for f in fields if f["name"] == "cards[abc-123][field2]")
        assert "less than 40 hours" in hours_f["label"].lower()
        assert form_data["answers"][str(hours_f["index"])] == "N/A (committed to full-time 40 hours/week)"

        # Check source radio group
        li_f = next(f for f in fields if f["name"] == "cards[abc-123][field3]" and "LinkedIn" in f["label"])
        assert "hear about" in li_f["group_label"].lower()
        assert form_data["answers"][str(li_f["index"])] == "yes"

    finally:
        server.shutdown()
        server.server_close()
