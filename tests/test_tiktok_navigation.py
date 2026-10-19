import pytest

from tiktok_navigation import (
    _AUTH_HEALTH_SCRIPT,
    _CLICK_QUEUE_SCRIPT,
    _QUEUE_DIAGNOSTIC_SCRIPT,
    _QUEUE_RESULTS_SETTLED_SCRIPT,
    NavigationError,
    open_review_queue,
)


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


class LockedQueuePage:
    """Click script keeps reporting that the queue card is absent."""

    url = "https://seller-sg.tiktok.com/product/rating"

    def goto(self, *args, **kwargs):
        return None

    def evaluate(self, script, *args):
        if script == _AUTH_HEALTH_SCRIPT:
            return {}
        if script == _CLICK_QUEUE_SCRIPT:
            return False
        if script == _QUEUE_DIAGNOSTIC_SCRIPT:
            return {"queue_card_found": False, "reply_now_count": 0, "ratings_headings": [], "row_count": 0, "row_star_counts": [], "path": "/product/rating"}
        raise AssertionError("unexpected page script")

    def wait_for_timeout(self, _milliseconds):
        return None


def test_missing_queue_card_error_names_the_step():
    page = LockedQueuePage()

    with pytest.raises(NavigationError) as caught:
        open_review_queue(
            page,
            "neutral",
            target_url="https://seller-sg.tiktok.com/product/rating?shop_region=SG",
            max_attempts=2,
            polls_per_attempt=1,
        )

    message = str(caught.value)
    assert "step=queue_card_not_found" in message
    assert "card_found=no" in message


class UnsettledResultsPage:
    """Click works and the Reply now action is found, but results never settle."""

    url = "https://seller-sg.tiktok.com/product/rating"

    def __init__(self):
        self.settle_polls = 0

    def goto(self, *args, **kwargs):
        return None

    def locator(self, selector):
        return FakeAction()

    def evaluate(self, script, *args):
        if script == _AUTH_HEALTH_SCRIPT:
            return {}
        if script == _CLICK_QUEUE_SCRIPT:
            return {"route": "dashboard", "expected_count": 7, "marker": "neutral"}
        if script == _QUEUE_RESULTS_SETTLED_SCRIPT:
            self.settle_polls += 1
            return False
        if script == _QUEUE_DIAGNOSTIC_SCRIPT:
            return {
                "queue_card_found": True,
                "reply_now_count": 1,
                "ratings_headings": ["20 Ratings"],
                "row_count": 10,
                "row_star_counts": [3],
                "path": "/product/rating",
            }
        raise AssertionError("unexpected page script")

    def wait_for_timeout(self, _milliseconds):
        return None


class FakeAction:
    def scroll_into_view_if_needed(self, **kwargs):
        return None

    def click(self, **kwargs):
        return None


def test_unsettled_results_error_names_the_step_and_count():
    page = UnsettledResultsPage()

    with pytest.raises(NavigationError) as caught:
        open_review_queue(
            page,
            "neutral",
            target_url="https://seller-sg.tiktok.com/product/rating?shop_region=SG",
            max_attempts=1,
            polls_per_attempt=1,
        )

    message = str(caught.value)
    assert page.settle_polls > 0
    assert "step=results_not_settled" in message
    assert "expected_count=7" in message
    assert "ratings_headings=['20 Ratings']" in message
    assert len(message) < 300
