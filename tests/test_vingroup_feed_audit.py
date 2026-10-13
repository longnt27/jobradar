"""Contract checks for Agent 1's Vingroup feed audit.

A searchable corporate page is not, by itself, a production career adapter.
Keep unresolved employers out of the seeded catalog until a listing/detail
round-trip is independently verified.
"""
import json
from pathlib import Path

from job_radar.feed_catalog import CAREER_FEEDS

OWNED = {
    "VinSpace", "VinSOC", "VinCSS", "GSM / Xanh SM", "V-GREEN",
    "VinBus", "Vinmec", "VinHMS", "VinID",
}


def test_agent_1_targets_have_explicit_evidence_and_no_pretend_adapters():
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "docs" / "career-feed-targets.json").read_text(
            encoding="utf-8"
        )
    )
    rows = {row["employer"]: row for row in manifest["targets"] if row["employer"] in OWNED}
    assert set(rows) == OWNED
    seeded = {feed.employer for feed in CAREER_FEEDS}
    for employer, row in rows.items():
        assert row["status"] in {"verified", "pending", "blocked"}
        assert row.get("evidence") and row.get("career_url")
        assert row.get("reviewed_at") == "2026-10-13"
        if row["status"] == "verified":
            assert employer in seeded and row.get("adapter")
        else:
            assert employer not in seeded
            assert row.get("adapter") is None


def test_unverified_subsidiaries_are_not_misrepresented_as_vingroup_api_entities():
    # The parent portal can include a mix of subsidiaries. Every subsidiary-specific
    # source needs its own authenticated/verified company code or recruitment feed.
    existing = {feed.employer: feed for feed in CAREER_FEEDS}
    for employer in OWNED - {"VinBus"}:
        assert employer not in existing
