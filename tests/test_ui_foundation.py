from pathlib import Path


STATIC = Path(__file__).parents[1] / "job_radar" / "static"


def test_ui_foundation_exposes_semantic_feedback_and_status_primitives() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'id="notice" class="toast" role="status" aria-live="polite" hidden' in html
    assert 'id="tab-loading" role="status" aria-live="polite" hidden' in html
    assert 'class="status-badge status-badge--warning"' in html
    assert 'id="setup-smtp-remove" class="danger"' in html
    assert 'id="setup-telegram-remove" class="danger"' in html

    assert ".status-badge--success" in css
    assert ".status-badge--warning" in css
    assert ".status-badge--danger" in css
    assert "button.danger" in css
    assert "#notice.toast" in css
    assert ".tab.is-loading" in css
    assert "@media(prefers-reduced-motion:reduce)" in css

    assert "node.setAttribute('role', error ? 'alert' : 'status')" in js
    assert "node.setAttribute('aria-live', error ? 'assertive' : 'polite')" in js
    assert "if (!error) window.noticeTimeout = setTimeout(clearNotice, 6000)" in js
    assert "function beginPending(button" in js
    assert "function setTabLoading(target, loading)" in js
    assert "function scrollNodeIntoView(node" in js


def test_ui_foundation_uses_readable_metadata_scale_and_semantic_surfaces() -> None:
    css = (STATIC / "app.css").read_text()
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()

    assert "--ui-muted:#526a62" in css
    assert ".item-meta,.hint,.empty" in css
    assert "font-size:13px" in css
    assert ".surface-readonly" in css
    assert ".surface-editable" in css
    assert ".surface-action" in css
    assert ".surface-status" in css

    assert 'class="panel job-analysis-panel surface-status"' in html
    assert 'id="job-detail" class="panel detail surface-readonly"' in html
    assert 'class="panel flow-panel surface-editable"' in html
    assert 'class="item job-card surface-action"' in js
    assert 'class="item clickable application-card surface-action"' in js
    assert 'data-state="rejected" class="danger"' in js
    assert 'data-state="ignored" class="danger"' in js
