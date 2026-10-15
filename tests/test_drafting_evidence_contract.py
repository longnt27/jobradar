import re

import pytest

from job_radar.drafting import (
    ModelDraft, ProjectBullets, ProjectFocus, _compose_application_message,
    _selected_resume_projects, _template,
)


PROFILE = {"name": "Alex", "experience": [
    {"role": "Applied AI Trainee / Team Lead", "company": "Training Program", "dates": "Jul 2026 - Aug 2026",
     "bullets": ["Led a 4-person team building booking agents."]},
    {"role": "AI Engineering Intern", "company": "Example Lab", "dates": "Dec 2025 - Jun 2026",
     "bullets": ["Built a reconstruction pipeline for robot training data."]},
]}
JOB = {"title": "AI Engineer", "company": "Example", "description": "Build AI systems.", "posting_source": {"kind": "career"}}


def test_template_fit_uses_engineering_work_instead_of_short_trainee_leadership():
    fit = _template(JOB, PROFILE, []).fit_text
    assert not re.search(r"team lead|led a|leadership|4-person", fit, re.I)
    assert "AI Engineering Intern" in fit
    assert "reconstruction" in fit.lower()


def test_generated_message_cannot_reintroduce_short_trainee_leadership():
    with pytest.raises(ValueError, match="leadership"):
        _compose_application_message(JOB, PROFILE, "I led a four-person team as Applied AI Trainee / Team Lead.")


@pytest.mark.parametrize("fit", ["I supervised an engineering team.", "I coordinated a team of five trainees."])
def test_message_rejects_people_management_claims(fit):
    with pytest.raises(ValueError, match="leadership"):
        _compose_application_message(JOB, PROFILE, fit)


def test_message_allows_leading_engineering_work():
    assert "led development" in _compose_application_message(JOB, PROFILE, "I led development of AI agents.")["body"]


def test_full_chatgpt_json_cannot_bypass_message_leadership_validation(monkeypatch):
    import json
    from unittest.mock import Mock
    from job_radar.chatgpt_handoff import _apply_all_reply
    monkeypatch.setattr("job_radar.chatgpt_handoff.get_preferences", lambda _: {})
    monkeypatch.setattr("job_radar.chatgpt_handoff.profile_with_preferences", lambda _: PROFILE)
    save = Mock()
    monkeypatch.setattr("job_radar.chatgpt_handoff.update_draft", save)
    db = Mock()
    db.all.return_value = []
    with pytest.raises(ValueError, match="leadership"):
        _apply_all_reply({"id": "draft", "resume_data": {"projects": []}}, json.dumps({
            "message_data": {"subject": "Application", "body": "I led a four-person team as Team Lead."},
        }), db, Mock())
    save.assert_not_called()


def test_full_chatgpt_reply_requires_fit_text_before_removing_starter_warning(monkeypatch):
    import json
    from unittest.mock import Mock
    from job_radar.chatgpt_handoff import _apply_all_reply

    monkeypatch.setattr("job_radar.chatgpt_handoff.get_preferences", lambda _: {})
    save = Mock()
    monkeypatch.setattr("job_radar.chatgpt_handoff.update_draft", save)
    db = Mock()
    db.all.return_value = []
    with pytest.raises(ValueError, match="fit_text"):
        _apply_all_reply({"id": "draft", "resume_data": {"projects": []}},
                         json.dumps({"summary": "AI engineer"}), db, Mock())
    save.assert_not_called()


def test_project_output_cannot_drop_primary_accuracy_or_dataset():
    card = {"id": "speech", "title": "Speech assessment", "claim": "Improved phone scoring", "details": {
        "tech_stack": ["Python", "PyTorch", "CTC"], "required_result_ids": ["accuracy"],
        "results": [
            {"id": "accuracy", "outcome": "Phone PCC 0.645 on SpeechOcean762", "dataset": "SpeechOcean762", "area": "Speech scoring"},
            {"id": "speed", "outcome": "1.24x faster on T4", "area": "Inference performance"},
        ],
    }}
    model = ModelDraft(selected_evidence_ids=["speech"], summary="Engineer", fit_text="I built AI systems.",
                       project_bullets=[ProjectBullets(evidence_id="speech", skills=["Python", "PyTorch", "CTC"],
                                                     bullets=["Built speech assessment", "Inference ran 1.24x faster on T4."])],
                       project_focus=[ProjectFocus(evidence_id="speech", result_ids=["speed"])])
    with pytest.raises(ValueError, match="primary|dataset"):
        _selected_resume_projects(JOB, [card], model, "codex", {"project_count": 1})


def test_template_keeps_reviewed_result_sentence_instead_of_keyword_selected_speed_only():
    card = {"id": "speech", "title": "Speech assessment", "kind": "project", "claim": "Phone PCC 0.645", "details": {
        "tech_stack": ["Python", "PyTorch", "CTC"],
        "resume_bullets": ["Built alignment-free speech scoring.", "On SpeechOcean762, phone PCC reached 0.645 versus 0.413 and T4 inference was 1.24x faster."],
        "results": [{"id": "speed", "area": "Systems", "outcome": "Inference was 1.24x faster."}],
    }}
    model = _template(JOB, PROFILE, [card])
    assert "SpeechOcean762" in model.project_bullets[0].bullets[1]
    assert "0.645" in model.project_bullets[0].bullets[1]


def test_template_preserves_all_primary_results_from_a_new_project_brief():
    card = {"id": "speech", "title": "Speech assessment", "kind": "project", "claim": "Alignment-free scoring", "details": {
        "what": "Built pronunciation scoring.", "tech_stack": ["Python"],
        "required_result_ids": ["accuracy", "speed"],
        "results": [
            {"id": "accuracy", "area": "Speech", "dataset": "SpeechOcean762", "required_metrics": ["0.645"], "outcome": "Phone PCC reached 0.645 on SpeechOcean762."},
            {"id": "speed", "area": "Systems", "required_metrics": ["1.24"], "outcome": "T4 inference was 1.24x faster."},
        ],
    }}
    model = _template(JOB, PROFILE, [card])
    _, projects = _selected_resume_projects(JOB, [card], model, "template")
    assert set(projects[0]["result_ids"]) == {"accuracy", "speed"}
    assert "SpeechOcean762" in projects[0]["bullets"][1]
    assert "1.24" in projects[0]["bullets"][1]
