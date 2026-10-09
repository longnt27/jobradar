from pathlib import Path

from pypdf import PdfReader

import pytest

from job_radar.resume_pdf import _body, editable_bullet_lines, render_resume
from job_radar.settings import Settings


def test_user_latex_template_keeps_vietnamese_text_and_escapes_resume_data(tmp_path: Path) -> None:
    resume = {
        "name": "Nguyễn Trung Long",
        "email": "long@example.com",
        "summary": "Built R&D tools with 100% reproducible outputs.",
        "experience": [{"company": "A&B", "role": "Engineer", "dates": "2025 – 2026",
                        "bullets": ["Improved accuracy to 95% on reviewed data."]}],
        "projects": [{"title": "Project_1", "repository_url": "https://github.com/example/project_1",
                      "tech_stack": ["Python", "R&D"],
                      "bullets": ["Built a model with Python.", "Reached 95% recall on reviewed data."]}],
        "education": [{"school": "Đại học Bách khoa Hà Nội", "degree": "Computer Science", "dates": "2022 – 2026"}],
        "bold_phrases": ["95% recall"],
    }
    source = _body(resume)
    assert r"A\&B" in source and r"Project\_1" in source
    assert r"\textbf{95\% recall}" in source
    path, _ = render_resume(Settings(tmp_path), "accented", resume)
    reader = PdfReader(path)
    text = reader.pages[0].extract_text()
    assert len(reader.pages) == 1
    assert "Nguyễn Trung Long" in text
    assert "Đại học Bách khoa Hà Nội" in text
    assert "A&B" in text and "100%" in text
    assert "example/project_1" in text
    assert reader.pages[0].get("/Annots")


def test_resume_bullets_accept_editable_latex_items(tmp_path: Path) -> None:
    resume = {
        "name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Example Labs", "role": "Engineer", "dates": "2026",
                        "bullets": [r"\item Built \textbf{Python systems} for search."]}],
        "projects": [{"title": "Search", "bullets": ["Built a search index.",
                      r"\item Reached \textbf{95\% recall} on reviewed data."]}],
        "achievements": [r"\item Won a \textbf{programming award}."],
    }
    source = editable_bullet_lines(resume)
    assert source["experience"][0] == [r"\item Built \textbf{Python systems} for search."]
    assert source["projects"][0][0] == r"\item Built a search index."
    assert r"\item Reached \textbf{95\% recall}" in _body(resume)
    path, _ = render_resume(Settings(tmp_path), "items", resume)
    extracted = PdfReader(path).pages[0].extract_text()
    assert "Python systems" in extracted and "95% recall" in extracted
    resume["experience"][0]["bullets"] = [r"\item \input{/etc/passwd}"]
    with pytest.raises(ValueError, match="Unsupported LaTeX command"):
        render_resume(Settings(tmp_path), "unsafe", resume)
    resume["experience"][0]["bullets"] = [r"\item Reached 95% recall."]
    with pytest.raises(ValueError, match="Escape LaTeX special characters"):
        render_resume(Settings(tmp_path), "percent", resume)
