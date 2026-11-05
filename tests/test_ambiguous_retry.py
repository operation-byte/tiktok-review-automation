import csv
from datetime import datetime, timedelta

import auto_reply_reviews as reviews

KEY = "snackersbox|negative|111|@buyer|abc"


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=reviews.LOG_FIELDS)
        writer.writeheader()
        for when, status in rows:
            writer.writerow({
                "replied_at": when.isoformat(timespec="seconds"), "profile": "snackersbox",
                "queue": "negative", "rating_filter": "1,2", "order_id": "111",
                "username": "@buyer", "product": "Snack", "review_text": "bad",
                "reply_text": "sorry", "status": status, "review_key": KEY,
            })


NOW = datetime.now().astimezone()


def test_single_old_ambiguous_attempt_may_retry(tmp_path):
    path = tmp_path / "log.csv"
    _write(path, [(NOW - timedelta(minutes=45), "ambiguous_submit_manual_check")])
    assert reviews.load_logged_review_states(path)[KEY] is reviews.LoggedReviewState.AMBIGUOUS_MANUAL_CHECK
    assert reviews.ambiguous_retry_allowed(reviews.ambiguous_attempts(path).get(KEY), NOW)


def test_recent_ambiguous_attempt_stays_blocked(tmp_path):
    path = tmp_path / "log.csv"
    _write(path, [(NOW - timedelta(minutes=5), "ambiguous_submit_manual_check")])
    assert not reviews.ambiguous_retry_allowed(reviews.ambiguous_attempts(path).get(KEY), NOW)


def test_second_ambiguous_attempt_needs_a_human(tmp_path):
    path = tmp_path / "log.csv"
    _write(path, [
        (NOW - timedelta(hours=5), "ambiguous_submit_manual_check"),
        (NOW - timedelta(hours=2), "ambiguous_submit_manual_check"),
    ])
    assert reviews.ambiguous_attempts(path)[KEY][0] == 2
    assert not reviews.ambiguous_retry_allowed(reviews.ambiguous_attempts(path).get(KEY), NOW)


def test_retry_that_succeeds_is_recorded_as_submitted(tmp_path):
    path = tmp_path / "log.csv"
    _write(path, [
        (NOW - timedelta(hours=1), "ambiguous_submit_manual_check"),
        (NOW, "submitted"),
    ])
    assert reviews.load_logged_review_states(path)[KEY] is reviews.LoggedReviewState.SUBMITTED


def test_unparseable_time_never_retries(tmp_path):
    path = tmp_path / "log.csv"
    _write(path, [(NOW, "ambiguous_submit_manual_check")])
    text = path.read_text().replace(NOW.isoformat(timespec="seconds"), "not-a-date")
    path.write_text(text)
    assert not reviews.ambiguous_retry_allowed(reviews.ambiguous_attempts(path).get(KEY), NOW)


def test_already_replied_is_never_retried(tmp_path):
    path = tmp_path / "log.csv"
    _write(path, [
        (NOW - timedelta(hours=1), "ambiguous_submit_manual_check"),
        (NOW, "already_replied"),
    ])
    assert reviews.load_logged_review_states(path)[KEY] is reviews.LoggedReviewState.ALREADY_REPLIED
