import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from job_radar.chatgpt_handoff import application_prompt, apply_chatgpt_reply
from job_radar.drafting import ModelDraft, ProjectBullets, _run_provider, _selected_resume_projects, prepare_draft, regenerate_draft, render_resume
from job_radar.settings import Settings
from job_radar.web import create_app
from job_radar.writing_preferences import DEFAULTS, get_preferences, render_prompt


def entries(client):
    return {item['key']: item for item in client.get('/api/writing-preferences').json()['entries']}


def save(client, key, value):
    result = client.put('/api/writing-preferences/' + key, json={'value': value})
    assert result.status_code == 200, result.text


def candidate(client):
    client.put('/api/profile', json={'name': 'Alex Example', 'email': 'alex@example.org',
                                   'summary': 'Engineer building reliable search systems.', 'skills': ['Python']})
    identifiers = []
    for index in range(3):
        card = client.post('/api/evidence', json={
            'kind': 'project', 'title': f'Search {index}', 'claim': 'Built a Python search system.',
            'approved': True,
        }).json()
        client.patch('/api/evidence/' + card['id'], json={'details': {
            'what': 'Built a Python search system.', 'tech_stack': ['Python', 'Search', 'Evaluation'],
            'results': [{'id': 'accuracy', 'area': 'Search', 'outcome': 'Reached 95% accuracy.', 'source': 'Reviewed results'},
                        {'id': 'latency', 'area': 'Search', 'outcome': 'Reduced latency by 40%.', 'source': 'Reviewed results'}],
        }})
        identifiers.append(card['id'])
    job = client.post('/api/jobs/import', json={
        'company': 'Example Labs', 'title': 'Search Engineer',
        'description': 'Build Python search systems.', 'apply_url': 'https://example.org/apply',
    }).json()
    return job['id'], identifiers


def test_customizations_persist_locally_without_changing_defaults_or_existing_settings(tmp_path):
    settings = Settings(tmp_path / 'alex')
    client = TestClient(create_app(settings))
    db = client.app.state.db
    original_profile = db.get_setting('profile')
    db.set_setting('zalo_inquiry_template', 'My existing {position} inquiry in {group}.')
    initial = entries(client)
    assert all(item['value'] == item['default'] and not item['customized'] for item in initial.values())
    save(client, 'section_summary', 'Use a direct, warm summary for a designer.')
    save(client, 'email_subject_en', '{title} | {name}')

    restarted = TestClient(create_app(settings))
    assert entries(restarted)['section_summary']['value'] == 'Use a direct, warm summary for a designer.'
    assert restarted.app.state.db.get_setting('profile') == original_profile
    assert restarted.get('/api/setup/zalo-template').json()['template'] == 'My existing {position} inquiry in {group}.'
    other = TestClient(create_app(Settings(tmp_path / 'another-person')))
    assert entries(other)['section_summary'] == initial['section_summary']
    assert DEFAULTS['section_summary']['default'] == initial['section_summary']['default']
    assert restarted.delete('/api/writing-preferences/section_summary').status_code == 200
    restored = entries(restarted)
    assert restored['section_summary'] == initial['section_summary']
    assert restored['email_subject_en']['customized']
    save(restarted, 'email_subject_en', restored['email_subject_en']['default'])
    assert get_preferences(restarted.app.state.db) == {}


@pytest.mark.parametrize('key,value', [
    ('unknown', 'text'), ('project_count', 0), ('project_count', 7), ('project_count', True),
    ('project_count', 2.5), ('project_bullet_count', 1), ('resume_max_pages', 4),
    ('section_summary', ''), ('email_body_en', 'Hello {unknown}'),
    ('email_subject_en', 'Subject\nInjected header'), ('resume_layout', r'\begin{document}__BODY__\end{document}'),
])
def test_invalid_preferences_are_rejected_without_writing(tmp_path, key, value):
    client = TestClient(create_app(Settings(tmp_path)))
    result = client.put('/api/writing-preferences/' + key, json={'value': value})
    assert result.status_code == 422
    assert get_preferences(client.app.state.db) == {}


def test_cli_drafts_honor_custom_prompts_structure_and_email_templates(tmp_path, monkeypatch):
    client = TestClient(create_app(Settings(tmp_path)))
    job_id, ids = candidate(client)
    original = prepare_draft(client.app.state.db, client.app.state.settings, job_id, 'template')
    save(client, 'application_draft', 'Write in a warm style. Select {project_count} projects with {project_bullet_count} bullets. {application_fit}')
    save(client, 'project_count', 2)
    save(client, 'project_bullet_count', 3)
    save(client, 'email_subject_en', '{title} | {name}')
    save(client, 'email_body_en', 'Hello {company},\n\n{fit}\n\nThank you,\n{name}')
    captured = []

    def provider(_provider, prompt, response_type):
        captured.append(prompt)
        return ModelDraft(
            selected_evidence_ids=ids[:2], summary='Search engineer.', fit_text='I built reliable search systems.',
            project_bullets=[ProjectBullets(evidence_id=identifier,
                bullets=['Built a Python search system.', 'Reached 95% accuracy.', 'Reduced latency by 40%.'],
                skills=['Python', 'Search', 'Evaluation']) for identifier in ids[:2]],
            bold_phrases=['95%', '40%'],
        )

    monkeypatch.setattr('job_radar.drafting._provider_json', provider)
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job_id, 'codex')
    assert len(draft['resume_data']['projects']) == 2
    assert all(len(item['bullets']) == 3 for item in draft['resume_data']['projects'])
    assert '40%' in draft['resume_data']['bold_phrases']
    assert draft['message_data'] == {'subject': 'Search Engineer | Alex Example',
        'body': 'Hello Example Labs,\n\nI built reliable search systems.\n\nThank you,\nAlex Example'}
    assert 'Write in a warm style. Select two projects with three bullets.' in captured[0]
    assert 'Return only the requested JSON schema' in captured[0]
    assert '"approved_projects"' in captured[0] and '"company": "Example Labs"' in captured[0]
    unchanged = client.get('/api/applications/' + original['id']).json()
    assert unchanged['resume_data'] == original['resume_data']
    assert unchanged['resume_hash'] == original['resume_hash']
    assert Path(original['resume_path']).is_file()


def test_section_and_chatgpt_regeneration_use_saved_prompts_and_preserve_custom_counts(tmp_path, monkeypatch):
    client = TestClient(create_app(Settings(tmp_path)))
    job_id, ids = candidate(client)
    db, settings = client.app.state.db, client.app.state.settings
    draft = prepare_draft(db, settings, job_id, 'chatgpt_web')
    save(client, 'project_count', 1)
    save(client, 'project_bullet_count', 3)
    save(client, 'chatgpt_projects', 'Prefer a compact portfolio with {project_count} project and {project_bullet_count} bullets.')
    prompt = application_prompt(db, draft['id'], 'projects', 'Focus on search.')
    assert 'Prefer a compact portfolio with one project and three bullets.' in prompt
    assert 'exactly three approved projects' not in prompt
    assert 'Focus on search.' in prompt
    revised = apply_chatgpt_reply(db, settings, draft['id'], 'projects', json.dumps({'projects': [
        {'id': ids[0], 'bullets': ['Built search.', 'Reached 95% accuracy.', 'Reduced latency by 40%.']},
    ]}))
    assert len(revised['resume_data']['projects']) == 1
    assert len(revised['resume_data']['projects'][0]['bullets']) == 3
    with pytest.raises(ValueError, match='fewer than 3 project bullets'):
        apply_chatgpt_reply(db, settings, draft['id'], 'projects', json.dumps({'projects': [
            {'id': ids[0], 'bullets': ['Built search.', 'Reached 95% accuracy.']},
        ]}))
    # The full-resume reply follows the same preferences and does not backfill to three.
    revised = apply_chatgpt_reply(db, settings, draft['id'], 'all', json.dumps({'summary': 'A revised search engineer.',
        'fit_text': 'I built search systems and measured project results.', 'projects': [
        {'id': ids[0], 'bullets': ['Built search.', 'Reached 95% accuracy.', 'Reduced latency by 40%.']},
    ]}))
    assert len(revised['resume_data']['projects']) == 1
    assert len(revised['resume_data']['projects'][0]['bullets']) == 3

    save(client, 'section_summary', 'Use a short designer summary.')
    captured = []
    def section(_provider, prompt, response_type):
        captured.append(prompt)
        return response_type(summary='Designer building reliable systems.')
    monkeypatch.setattr('job_radar.drafting._provider_json', section)
    revised = regenerate_draft(db, settings, draft['id'], 'Improve clarity.', 'summary')
    assert revised['resume_data']['summary'] == 'Designer building reliable systems.'
    assert 'Use a short designer summary.' in captured[0]
    assert 'Applied AI Engineer' not in captured[0]


def test_custom_layout_is_rendered_and_original_pdf_is_retained(tmp_path):
    client = TestClient(create_app(Settings(tmp_path)))
    job_id, _ = candidate(client)
    original = prepare_draft(client.app.state.db, client.app.state.settings, job_id, 'template')
    original_bytes = Path(original['resume_path']).read_bytes()
    layout = entries(client)['resume_layout']['default']
    save(client, 'resume_layout', layout.replace(r'\definecolor{headingcolor}{RGB}{30,30,30}',
                                               r'\definecolor{headingcolor}{RGB}{20,60,120}'))
    revised_path, _ = render_resume(client.app.state.settings, original['id'], original['resume_data'])
    assert revised_path != original['resume_path']
    assert Path(original['resume_path']).read_bytes() == original_bytes
    assert 'Alex Example' in PdfReader(revised_path).pages[0].extract_text()
    assert client.delete('/api/writing-preferences/resume_layout').status_code == 200
    restored_path, _ = render_resume(client.app.state.settings, original['id'], original['resume_data'])
    assert restored_path == original['resume_path']


def test_template_substitution_does_not_interpret_candidate_text_as_more_placeholders():
    preferences = {'email_body_en': 'Hello {company}. {fit}'}
    assert render_prompt(preferences, 'email_body_en', company='Literal {fit}', fit='Built search.') == 'Hello Literal {fit}. Built search.'


def test_page_limit_is_enforced_even_when_a_longer_pdf_was_cached(tmp_path):
    client = TestClient(create_app(Settings(tmp_path)))
    job_id, _ = candidate(client)
    settings = client.app.state.settings
    draft = prepare_draft(client.app.state.db, settings, job_id, 'template')
    layout = entries(client)['resume_layout']['default']
    save(client, 'resume_layout', layout.replace('__BODY__', '__BODY__\n\\newpage\nAdditional details.'))
    with pytest.raises(ValueError, match='maximum 1'):
        render_resume(settings, draft['id'], draft['resume_data'])
    save(client, 'resume_max_pages', 2)
    path, _ = render_resume(settings, draft['id'], draft['resume_data'])
    assert len(PdfReader(path).pages) == 2
    assert client.delete('/api/writing-preferences/resume_max_pages').status_code == 200
    with pytest.raises(ValueError, match='maximum 1'):
        render_resume(settings, draft['id'], draft['resume_data'])
    # Settings changes do not delete the previously reviewed artifact.
    assert len(PdfReader(path).pages) == 2


def test_custom_structure_uses_fewer_available_projects_without_reverting_bullet_preferences(monkeypatch):
    preferences = {'project_count': 2, 'project_bullet_count': 3}
    cards = [{'id': 'one', 'title': 'Search', 'claim': 'Built search.', 'details': {}}]
    model = ModelDraft(selected_evidence_ids=['one'], summary='Engineer.', fit_text='I built search systems.',
        project_bullets=[ProjectBullets(evidence_id='one', skills=['Python', 'Search', 'Evaluation'],
                        bullets=['Built search.', 'Reached 95% accuracy.', 'Evaluated ranking quality.'])],
        bold_phrases=['95%'])
    monkeypatch.setattr('job_radar.drafting._provider_json', lambda *_args: model)
    result = _run_provider('codex', {'title': 'Engineer'}, {'writing_preferences': preferences}, cards)
    _, projects = _selected_resume_projects({}, cards, result, 'codex', preferences)
    assert len(projects) == 1 and len(projects[0]['bullets']) == 3
    model.project_bullets[0].bullets.pop()
    with pytest.raises(RuntimeError, match='omitted required projects'):
        _run_provider('codex', {'title': 'Engineer'}, {'writing_preferences': preferences}, cards)
