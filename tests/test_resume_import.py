from pathlib import Path
from io import BytesIO
import shutil

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from job_radar.resume_extract import read_resume_pdf
from job_radar.settings import Settings
from job_radar.web import create_app


def test_fresh_profile_requires_provider_before_pdf_import(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    assert profile["name"] == ""
    assert profile["location"] == ""
    assert profile["experience"] == []
    assert profile["drafting_provider"] == ""
    assert client.get("/api/evidence").json() == []
    response = client.post("/api/profile/resume/pdf", files={"file": ("resume.pdf", b"%PDF-", "application/pdf")})
    assert response.status_code == 409
    assert "Profile" in response.json()["detail"]


def test_pdf_reader_accepts_real_text_pdf_and_rejects_other_files() -> None:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 720, "Candidate Name | candidate@example.org | Python engineer")
    pdf.drawString(72, 700, "Experience: Example Company, Software Engineer, 2023 to 2025")
    pdf.drawString(72, 680, "Built search services with Python and evaluated ranking quality.")
    pdf.save()
    text = read_resume_pdf(output.getvalue())
    assert "Example Company" in text
    try:
        read_resume_pdf(b"not a PDF")
    except ValueError as error:
        assert "PDF" in str(error)
    else:
        raise AssertionError("Non-PDF upload was accepted")


def test_provider_selection_persists_when_available(tmp_path: Path) -> None:
    provider = next((name for name in ("codex", "agy", "claude") if shutil.which(name)), None)
    if provider is None:
        return
    client = TestClient(create_app(Settings(tmp_path)))
    response = client.put("/api/profile/provider", json={"provider": provider})
    assert response.status_code == 200, response.text
    assert client.get("/api/profile").json()["drafting_provider"] == provider
    assert client.get("/api/setup").json()["selected_provider"] == provider
    invalid = client.post("/api/profile/resume/pdf", files={"file": ("resume.pdf", b"not a PDF", "application/pdf")})
    assert invalid.status_code == 422
    assert "PDF" in invalid.json()["detail"]


def test_latex_import_separates_positions_from_projects(tmp_path: Path) -> None:
    latex = r"""\begin{document}
\begin{center}\name{Alex Example} \contactitem{\faEnvelope}{\href{mailto:alex@example.org}{alex@example.org}}\end{center}
\noindent\textit{AI engineer focused on search.}
\section{Experience}
\begin{expentry}{Example Labs}{2024 -- 2026}{AI Engineer}
\begin{itemize}\item Built a \textbf{Python} search service.\end{itemize}
\end{expentry}
\section{Selected Projects}
\begin{projentry}{Search repo}{Python}\begin{itemize}\item Built indexing.\end{itemize}\end{projentry}
\section{Education}
\textbf{Example University} & 2022 -- 2026 \\
BSc Computer Science &
\section{Achievements}
\begin{itemize}\item Programming award\end{itemize}
\section{Skills}
Programming & Python, C++
\end{document}"""
    client = TestClient(create_app(Settings(tmp_path)))
    response = client.post("/api/profile/import-latex", json={"latex": latex})
    assert response.status_code == 200, response.text
    profile = client.get("/api/profile").json()
    assert profile["experience"][0]["company"] == "Example Labs"
    assert profile["experience"][0]["bullets"] == ["Built a Python search service."]
    assert profile["education"][0]["school"] == "Example University"
    assert profile["skill_groups"]["Programming"] == "Python, C++"
    assert client.get("/api/evidence").json() == []
