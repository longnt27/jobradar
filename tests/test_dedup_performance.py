import pytest

from job_radar import dedup


@pytest.mark.parametrize("company,title", [("Other employer", "Senior AI Engineer"),
                                          ("Example", "Intern AI Engineer")])
def test_incompatible_jobs_are_rejected_before_parsing_their_descriptions(monkeypatch, company, title):
    parsed = []
    original = dedup._content

    def content(value):
        parsed.append(value)
        return original(value)

    monkeypatch.setattr(dedup, "_content", content)
    left = {"id": "left", "company": "Example", "title": "Senior AI Engineer",
            "description": "Build production Python machine learning systems."}
    right = {"id": "right", "company": company, "title": title,
             "description": "Build production Python machine learning systems."}

    assert dedup.assess(left, right, {}) is None
    assert parsed == []
