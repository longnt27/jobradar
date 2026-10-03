from bs4 import BeautifulSoup

from job_radar.collectors import _posting_description


def test_career_description_keeps_headings_paragraphs_and_bullets() -> None:
    html = """<div class="careers_detail_contents">
      <div class="the_content">
        <p>Build <strong>embedded software</strong> for a vehicle platform.</p>
        <h4>What you’ll do</h4>
        <ul><li>Design C++ components.</li><li>Review system requirements.</li></ul>
        <h4>What you’ll need</h4>
        <p>Experience with Linux.<br>Clear written communication.</p>
      </div>
    </div>"""

    description = _posting_description(BeautifulSoup(html, "html.parser"))

    assert "Build embedded software for a vehicle platform." in description
    assert "What you’ll do\n\n• Design C++ components.\n• Review system requirements." in description
    assert "What you’ll need\n\nExperience with Linux.\nClear written communication." in description
