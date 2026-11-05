import reply_submission as rs
from review_detection import PageScan, PageState, ReviewCard


CARD = ReviewCard(
    key="snackersbox|negative|111|@buyer|abc", order_id="111", username="@buyer",
    product_id="222", product="Snack", review_text="stale", review_date="1 Nov 2026",
    rating=1, response_count=0, has_reply=True, text="",
)


class FakePage:
    def __init__(self, toasts_by_call):
        self._toasts = list(toasts_by_call)
        self.waits = 0

    def wait_for_timeout(self, _ms):
        self.waits += 1


def _patch(monkeypatch, page):
    monkeypatch.setattr(rs, "_open_editor_for_identity", lambda *_: True)
    monkeypatch.setattr(rs, "_fill_editor", lambda *_: True)
    monkeypatch.setattr(rs, "_click_submit", lambda *_: True)
    monkeypatch.setattr(rs, "_close_existing_reply_dialog", lambda *_: False)

    def toasts(_page):
        return page._toasts.pop(0) if len(page._toasts) > 1 else page._toasts[0]

    monkeypatch.setattr(rs, "_success_acknowledgements", toasts)


def _absent():
    # Under "Awaiting reply" a replied row drops out of the list.
    return PageScan(PageState.LOADED_EMPTY)


def test_late_success_toast_counts_as_submitted(monkeypatch):
    # Baseline, then two checks with no toast yet, then the toast appears.
    page = FakePage([(), (), (), ("Reply sent successfully|",)])
    _patch(monkeypatch, page)
    result = rs.submit_reply(page, CARD, "sorry", submit=True, rescan=_absent)
    assert result.outcome is rs.SubmissionOutcome.SUBMITTED
    assert result.positive_signal == "success_acknowledgement"


def test_no_toast_and_absent_row_stays_ambiguous(monkeypatch):
    page = FakePage([()])
    _patch(monkeypatch, page)
    result = rs.submit_reply(page, CARD, "sorry", submit=True, rescan=_absent)
    assert result.outcome is rs.SubmissionOutcome.AMBIGUOUS
    assert "absent" in result.reason


def test_stale_toast_is_not_success(monkeypatch):
    # The same toast already on screen before the click proves nothing.
    page = FakePage([("Reply sent successfully|",)])
    _patch(monkeypatch, page)
    result = rs.submit_reply(page, CARD, "sorry", submit=True, rescan=_absent)
    assert result.outcome is rs.SubmissionOutcome.AMBIGUOUS


def test_missing_baseline_never_trusts_toast(monkeypatch):
    page = FakePage([("Reply sent successfully|",)])
    _patch(monkeypatch, page)
    calls = {"n": 0}

    def toasts(_page):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("no baseline")
        return ("Reply sent successfully|",)

    monkeypatch.setattr(rs, "_success_acknowledgements", toasts)
    result = rs.submit_reply(page, CARD, "sorry", submit=True, rescan=_absent)
    assert result.outcome is rs.SubmissionOutcome.AMBIGUOUS


def test_existing_reply_dialog_is_already_replied_and_never_submits(monkeypatch):
    page = FakePage([()])
    _patch(monkeypatch, page)
    monkeypatch.setattr(rs, "_close_existing_reply_dialog", lambda *_: True)
    clicked = []
    monkeypatch.setattr(rs, "_click_submit", lambda *_: clicked.append(1) or True)
    filled = []
    monkeypatch.setattr(rs, "_fill_editor", lambda *_: filled.append(1) or True)
    result = rs.submit_reply(page, CARD, "sorry", submit=True, rescan=_absent)
    assert result.outcome is rs.SubmissionOutcome.ALREADY_REPLIED
    assert not clicked and not filled


def test_dialog_that_loads_late_is_still_recognised(monkeypatch):
    page = FakePage([()])
    _patch(monkeypatch, page)
    seen = iter([False, True])
    monkeypatch.setattr(rs, "_close_existing_reply_dialog", lambda *_: next(seen))
    monkeypatch.setattr(rs, "_fill_editor", lambda *_: False)
    result = rs.submit_reply(page, CARD, "sorry", submit=True, rescan=_absent)
    assert result.outcome is rs.SubmissionOutcome.ALREADY_REPLIED


def test_no_editor_and_no_dialog_is_a_failure(monkeypatch):
    page = FakePage([()])
    _patch(monkeypatch, page)
    monkeypatch.setattr(rs, "_fill_editor", lambda *_: False)
    monkeypatch.setattr(rs, "_editor_diagnostics", lambda *_: "")
    result = rs.submit_reply(page, CARD, "sorry", submit=True, rescan=_absent)
    assert result.outcome is rs.SubmissionOutcome.FAILED
