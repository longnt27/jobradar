from pathlib import Path

from pypdf import PdfReader

from job_radar.resume_pdf import _body, render_resume
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
