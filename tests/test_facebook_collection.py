from job_radar.collectors import _facebook_post_url


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
