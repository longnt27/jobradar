from types import SimpleNamespace

from pydantic import BaseModel

from job_radar.drafting import _provider_json, provider_model_override


class Answer(BaseModel):
    answer: str


def test_selected_model_reaches_cli(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr('job_radar.drafting.shutil.which', lambda command: f'/usr/bin/{command}')

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"answer":"ok"}', stderr='')

    monkeypatch.setattr('job_radar.drafting.subprocess.run', run)
    token = provider_model_override.set('test-model')
    try:
        assert _provider_json('codex', 'prompt', Answer).answer == 'ok'
        assert _provider_json('claude', 'prompt', Answer).answer == 'ok'
    finally:
        provider_model_override.reset(token)
    assert all(args[args.index('--model') + 1] == 'test-model' for args in calls)
    assert calls[0].index('--model') < calls[0].index('-')
    assert calls[1].index('--model') < calls[1].index('prompt')
