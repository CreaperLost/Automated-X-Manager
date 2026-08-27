"""Cost estimation: link-in-reply vs inline URL."""
from x_auto.x.costs import (
    COST_POST_PLAIN,
    COST_POST_WITH_URL,
    SessionMeter,
    estimate_post_cost,
    estimate_read_cost,
)


class TestEstimatePostCost:
    def test_plain_no_reply(self):
        b = estimate_post_cost("hello world", link_in_reply=False)
        assert b.main == COST_POST_PLAIN
        assert b.reply == 0.0
        assert b.total == COST_POST_PLAIN

    def test_plain_with_reply(self):
        b = estimate_post_cost("hello world", link_in_reply=True, reply_text="https://x.com")
        assert b.main == COST_POST_PLAIN
        # Reply cost: $0.015 (we send the URL as a separate post)
        assert b.reply == COST_POST_PLAIN
        assert b.total == 2 * COST_POST_PLAIN
        assert b.saved > 0

    def test_inline_url(self):
        b = estimate_post_cost("see https://x.com for more", link_in_reply=False)
        assert b.main == COST_POST_WITH_URL
        assert b.reply == 0.0
        assert b.total == COST_POST_WITH_URL
        assert "surcharge" in b.reason

    def test_link_in_reply_saves_money(self):
        body = "look at this thing"
        inline = estimate_post_cost(body + " https://x.com", link_in_reply=False)
        thread = estimate_post_cost(body, link_in_reply=True, reply_text="https://x.com")
        assert thread.total < inline.total
        assert abs(thread.saved - (inline.total - thread.total)) < 1e-6

    def test_image_only(self):
        b = estimate_post_cost("look", has_image=True, link_in_reply=False)
        assert b.main == COST_POST_PLAIN  # image doesn't change the post cost tier

    def test_with_image_and_reply(self):
        b = estimate_post_cost(
            "look", has_image=True, link_in_reply=True, reply_text="https://x.com"
        )
        assert b.total == 2 * COST_POST_PLAIN


class TestEstimateReadCost:
    def test_zero(self):
        assert estimate_read_cost(0, 0) == 0.0

    def test_posts_only(self):
        assert estimate_read_cost(10, 0) == 10 * 0.005

    def test_profiles_only(self):
        assert estimate_read_cost(0, 3) == 3 * 0.010

    def test_mixed(self):
        cost = estimate_read_cost(20, 2)
        assert cost == 20 * 0.005 + 2 * 0.010


class TestSessionMeter:
    def test_empty(self):
        m = SessionMeter()
        assert m.total() == 0.0
        assert m.reads_cost() == 0.0

    def test_adds(self):
        m = SessionMeter()
        m.add_read_post(10)
        m.add_read_profile(2)
        m.add_write(0.030)
        assert m.reads_cost() == 10 * 0.005 + 2 * 0.010
        assert m.total() == m.reads_cost() + 0.030

    def test_summary(self):
        m = SessionMeter()
        m.add_read_post(1)
        s = m.summary()
        assert s["posts_read"] == 1
        assert "total_cost_usd" in s
        assert "writes_cost_usd" in s

    def test_reset(self):
        m = SessionMeter()
        m.add_read_post(5)
        m.reset()
        assert m.total() == 0.0
