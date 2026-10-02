from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.web import create_app


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
