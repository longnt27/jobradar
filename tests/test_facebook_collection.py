from job_radar.collectors import RECRUITING, _facebook_post_url, _facebook_posted_at


def test_facebook_post_links_from_group_card() -> None:
    group = "https://www.facebook.com/groups/1407434203194440/"
    assert _facebook_post_url(group, [
        "https://www.facebook.com/groups/1407434203194440/posts/123/?comment_id=456",
    ]) == "https://www.facebook.com/groups/1407434203194440/posts/123/"
    assert _facebook_post_url(group, [
        "https://www.facebook.com/photo/?fbid=9&set=gm.456&idorvanity=1407434203194440",
    ]) == "https://www.facebook.com/groups/1407434203194440/posts/456/"
    assert _facebook_post_url(group, [
        "https://www.facebook.com/groups/other/posts/789/",
        "https://www.facebook.com/photo/?set=gm.789&idorvanity=other",
    ]) is None


def test_facebook_timestamp_tooltip_is_saved_as_posting_date() -> None:
    assert _facebook_posted_at("Tuesday 29 September 2026 at 15:50") == "2026-09-29T08:50:00+00:00"
    assert _facebook_posted_at("5 days ago") is None
    assert RECRUITING.search("VMO cần gấp AI Engineer có KN về GenAI")
