from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Callable

from review_detection import PageScan, PageState, ReviewCard


class ItemAction(str, Enum):
    REPLY = "reply"
    SKIP_OLD = "skip_old"
    SKIP_LOGGED = "skip_logged"
    HOLD_MANUAL = "hold_manual"
    BLOCK_MANUAL = "block_manual"
    # TikTok confirmed a seller reply exists, but its list may still show the
    # row as awaiting reply. Account for it; never reply again.
    SKIP_REPLIED = "skip_replied"


@dataclass(frozen=True)
class ItemDecision:
    action: ItemAction
    reply_text: str = ""


class QueueOutcome(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    LOGIN_REQUIRED = "login_required"
    BROWSER_CLOSED = "browser_closed"


@dataclass(frozen=True)
class QueueResult:
    outcome: QueueOutcome
    submitted: int = 0
    skipped_old: int = 0
    skipped_logged: int = 0
    held_manual: int = 0
    dry_run: int = 0
    manual_check: int = 0
    already_replied: int = 0
    failed: int = 0
    remaining: int = 0
    dashboard_count: int | None = None
    reconciliation_notes: tuple[str, ...] = ()
    diagnostic_paths: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class _Progress:
    submitted: set[str] = field(default_factory=set)
    skipped_old: set[str] = field(default_factory=set)
    skipped_logged: set[str] = field(default_factory=set)
    held_manual: set[str] = field(default_factory=set)
    dry_run: set[str] = field(default_factory=set)
    manual_check: set[str] = field(default_factory=set)
    confirmed_replied: set[str] = field(default_factory=set)

    @property
    def terminal(self) -> set[str]:
        return (self.submitted | self.skipped_old | self.held_manual | self.dry_run
                | self.manual_check | self.confirmed_replied)


def _result(progress: _Progress, outcome: QueueOutcome, reason: str = "", *, failed: int = 0,
            remaining: int = 0, dashboard_count: int | None = None,
            notes: tuple[str, ...] = ()) -> QueueResult:
    return QueueResult(outcome=outcome, submitted=len(progress.submitted),
                       skipped_old=len(progress.skipped_old), skipped_logged=len(progress.skipped_logged),
                       held_manual=len(progress.held_manual), dry_run=len(progress.dry_run),
                       already_replied=len(progress.confirmed_replied),
                       manual_check=len(progress.manual_check), failed=failed, remaining=remaining,
                       dashboard_count=dashboard_count, reconciliation_notes=notes, reason=reason)


def process_queue(scan: Callable[[], PageScan], decide: Callable[[ReviewCard], ItemDecision],
                  submit: Callable[[ReviewCard, str], object], *, max_replies: int = 20,
                  max_cycles: int = 60, prepare_scan: Callable[[], None] | None = None,
                  wait_for_change: Callable[[], None] | None = None,
                  recover_on_empty: Callable[[], None] | None = None,
                  continue_dry_run: bool = False,
                  min_stable_seconds: float = 0.0,
                  clock: Callable[[], float] = time.monotonic,
                  _progress: _Progress | None = None) -> QueueResult:
    progress = _progress or _Progress()
    stable_clear_scans = 0
    empty_recovered = recover_on_empty is None
    empty_fingerprint = None
    empty_since = None
    for _ in range(max_cycles):
        if prepare_scan:
            prepare_scan()
        current = scan()
        if current.state is PageState.LOGIN_REQUIRED:
            return _result(progress, QueueOutcome.LOGIN_REQUIRED, current.detail)
        if current.state is PageState.BROWSER_CLOSED:
            return _result(progress, QueueOutcome.BROWSER_CLOSED, current.detail, remaining=1)
        if current.state is PageState.DETECTION_MISMATCH:
            return _result(progress, QueueOutcome.INCOMPLETE, current.detail or "review detection mismatch",
                           remaining=max(1, current.review_like_count))
        if current.state is PageState.LOADING:
            stable_clear_scans = 0
            if wait_for_change:
                wait_for_change()
            continue

        candidate = None
        logged_conflicts: set[str] = set()
        for card in current.cards:
            if card.key in progress.terminal:
                continue
            # The scanner records every visible review row for filter safety,
            # but only rows with a current Reply action may enter submission.
            if not card.has_reply:
                continue
            # Some views retain a Reply control even after a seller response.
            # The ledger is a second defence, not the only evidence of a reply.
            if card.response_count is None or card.response_count < 0:
                progress.held_manual.add(card.key)
                continue
            if card.response_count > 0:
                continue
            decision = decide(card)
            if decision.action is ItemAction.SKIP_OLD:
                progress.skipped_old.add(card.key)
            elif decision.action is ItemAction.SKIP_LOGGED:
                progress.skipped_logged.add(card.key)
                if card.has_reply:
                    logged_conflicts.add(card.key)
            elif decision.action is ItemAction.HOLD_MANUAL:
                progress.held_manual.add(card.key)
            elif decision.action is ItemAction.SKIP_REPLIED:
                progress.confirmed_replied.add(card.key)
            elif decision.action is ItemAction.BLOCK_MANUAL:
                progress.manual_check.add(card.key)
                return _result(progress, QueueOutcome.INCOMPLETE,
                               "review requires manual verification before processing",
                               remaining=1, notes=("terminal_manual_block",))
            elif decision.action is ItemAction.REPLY:
                candidate = (card, decision)
                break

        if logged_conflicts:
            return _result(progress, QueueOutcome.INCOMPLETE,
                           "logged review is still actionable; prior submission may be a false positive",
                           remaining=len(logged_conflicts), notes=("actionable_logged_mismatch",))
        if candidate is None:
            fingerprint = (current.state.value, tuple(sorted(card.key for card in current.cards)))
            if fingerprint == empty_fingerprint:
                stable_clear_scans += 1
            else:
                stable_clear_scans = 1
                empty_since = clock()
            empty_fingerprint = fingerprint
            if stable_clear_scans >= 2 and clock() - (empty_since or 0) >= min_stable_seconds:
                if not empty_recovered:
                    recover_on_empty()
                    empty_recovered = True
                    stable_clear_scans = 0
                    empty_fingerprint = None
                    empty_since = None
                else:
                    return _result(progress, QueueOutcome.COMPLETE)
            if wait_for_change:
                wait_for_change()
            continue

        stable_clear_scans = 0
        if len(progress.submitted) >= max_replies:
            return _result(progress, QueueOutcome.INCOMPLETE, "maximum replies safety cap reached", remaining=1)
        card, decision = candidate
        submission = submit(card, decision.reply_text)
        outcome = getattr(getattr(submission, "outcome", None), "value", getattr(submission, "outcome", ""))
        if outcome == "submitted":
            progress.submitted.add(card.key)
        elif outcome == "already_replied":
            # TikTok's list was stale; the review already has a seller reply.
            # Count it as accounted for so dashboard reconciliation still adds up.
            progress.confirmed_replied.add(card.key)
        elif outcome == "ambiguous":
            progress.manual_check.add(card.key)
            detail = getattr(submission, "reason", "")
            return _result(progress, QueueOutcome.INCOMPLETE,
                           "submission is ambiguous; manual verification required before retry"
                           + (f" ({detail})" if detail else ""),
                           remaining=1, notes=("ambiguous_manual_check",))
        elif outcome == "dry_run":
            progress.dry_run.add(card.key)
            if not continue_dry_run:
                return _result(progress, QueueOutcome.INCOMPLETE, "dry run inspected reply without submitting", remaining=1)
        else:
            return _result(progress, QueueOutcome.INCOMPLETE,
                           getattr(submission, "reason", "submission was not positively verified"),
                           failed=1, remaining=1)
        if wait_for_change:
            wait_for_change()
    return _result(progress, QueueOutcome.INCOMPLETE, "queue did not reach a stable completed state", remaining=1)


# Every run walks the queue from page 1, and held reviews stay listed, so a
# large backlog can span well over 20 pages. Override with REVIEW_QUEUE_MAX_PAGES.
DEFAULT_MAX_PAGES = 200


def process_paginated_queue(scan: Callable[[], PageScan], decide: Callable[[ReviewCard], ItemDecision],
                            submit: Callable[[ReviewCard, str], object], *, advance_page: Callable[[], bool],
                            max_replies: int = 20, max_cycles_per_page: int = 60,
                            max_pages: int = DEFAULT_MAX_PAGES,
                            prepare_scan: Callable[[], None] | None = None,
                            wait_for_change: Callable[[], None] | None = None,
                            recover_on_empty: Callable[[], None] | None = None,
                            dashboard_count: Callable[[], int | None] | None = None,
                            dashboard_wait: Callable[[], None] | None = None,
                            dashboard_retries: int = 3, queue_name: str = "",
                            min_stable_seconds: float = 0.0) -> QueueResult:
    progress = _Progress()
    previous_page_keys: frozenset[str] | None = None
    for pages_walked in range(1, max_pages + 1):
        page_keys: set[str] = set()

        def tracked_scan(_inner=scan, _keys=page_keys) -> PageScan:
            current = _inner()
            _keys.update(card.key for card in current.cards)
            return current

        page_result = process_queue(tracked_scan, decide, submit, max_replies=max_replies,
                                    max_cycles=max_cycles_per_page, prepare_scan=prepare_scan,
                                    wait_for_change=wait_for_change,
                                    recover_on_empty=recover_on_empty, continue_dry_run=True,
                                    min_stable_seconds=min_stable_seconds,
                                    _progress=progress)
        if page_result.outcome is not QueueOutcome.COMPLETE:
            return page_result
        # Next was clicked but the list still shows the same reviews: the paginator
        # is looping, which a larger cap would only hide.
        page_fingerprint = frozenset(page_keys) if page_keys else None
        if page_fingerprint is not None and page_fingerprint == previous_page_keys:
            return _result(progress, QueueOutcome.INCOMPLETE,
                           f"pagination repeated the same reviews after page {pages_walked}", remaining=1)
        previous_page_keys = page_fingerprint
        if not advance_page():
            break
    else:
        return _result(progress, QueueOutcome.INCOMPLETE,
                       f"pagination safety cap reached after {max_pages} pages", remaining=1)

    if progress.dry_run:
        return _result(progress, QueueOutcome.INCOMPLETE,
                       "dry run inspected all eligible reviews without submitting",
                       remaining=len(progress.dry_run))

    advertised = None
    explainable_keys = (progress.skipped_old | progress.skipped_logged | progress.held_manual
                        | progress.confirmed_replied)
    explainable = len(explainable_keys)
    for attempt in range(max(1, dashboard_retries) if dashboard_count else 0):
        advertised = dashboard_count()
        # A dashboard that lags lower than the list hides nothing; only a
        # count above the accounted-for items means reviews were missed.
        if advertised is not None and advertised <= explainable:
            break
        if attempt + 1 < dashboard_retries and dashboard_wait:
            dashboard_wait()
    if dashboard_count and advertised is None:
        return _result(progress, QueueOutcome.INCOMPLETE, "dashboard count unavailable for reconciliation",
                       remaining=1, notes=("dashboard_count_unavailable",))
    if advertised is not None and advertised > explainable:
        note = (f"dashboard {queue_name or 'queue'} count {advertised} exceeds "
                f"{explainable} known old/manual items")
        return _result(progress, QueueOutcome.INCOMPLETE, "dashboard count could not be reconciled",
                       remaining=max(1, advertised - explainable), dashboard_count=advertised, notes=(note,))
    return _result(progress, QueueOutcome.COMPLETE, dashboard_count=advertised)
