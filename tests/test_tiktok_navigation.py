from tiktok_navigation import _AUTH_HEALTH_SCRIPT, _CLICK_QUEUE_SCRIPT, open_review_queue


class EmptyQueuePage:
    url = "https://seller-sg.tiktok.com/product/rating"

    def goto(self, *args, **kwargs):
        return None

    def evaluate(self, script, *args):
        if script == _AUTH_HEALTH_SCRIPT:
            return {}
        if script == _CLICK_QUEUE_SCRIPT:
            return {"route": "dashboard", "expected_count": 0, "marker": None}
        raise AssertionError("unexpected page script")

    def wait_for_timeout(self, _milliseconds):
        return None


def test_empty_queue_does_not_require_reply_now_button():
    page = EmptyQueuePage()

    result = open_review_queue(
        page,
        "neutral",
        target_url="https://seller-sg.tiktok.com/product/rating?shop_region=SG",
        max_attempts=1,
        polls_per_attempt=1,
    )

    assert result.queue_name == "neutral"
    assert result.empty is True
