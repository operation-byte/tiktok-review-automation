"""Safe, identity-based TikTok review reply submission.

This module deliberately separates clicking Submit from declaring success.  A click
is only an attempt; a positive acknowledgement or a changed target card is proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from enum import Enum
from typing import Callable

from review_detection import PageScan, PageState, ReviewCard


class SubmissionOutcome(str, Enum):
    SUBMITTED = "submitted"
    DRY_RUN = "dry_run"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class SubmissionResult:
    outcome: SubmissionOutcome
    reason: str = ""
    positive_signal: str = ""

    @property
    def submitted(self) -> bool:
        return self.outcome is SubmissionOutcome.SUBMITTED


def submit_reply(
    page,
    card: ReviewCard,
    reply_text: str,
    *,
    submit: bool,
    rescan: Callable[[], PageScan],
) -> SubmissionResult:
    """Reply to *card*, returning SUBMITTED only with positive UI evidence."""
    try:
        opened = _open_editor_for_identity(page, card)
        if opened is not True:
            detail = ""
            if isinstance(opened, dict):
                detail = (f" (reply controls on page={opened.get('reply_controls')}, "
                          f"matching this review={opened.get('matching_rows')})")
            return SubmissionResult(SubmissionOutcome.NOT_FOUND, "stable review identity was not uniquely actionable" + detail)
        if not _fill_editor(page, reply_text):
            return SubmissionResult(SubmissionOutcome.FAILED, "visible reply editor was not available")
        if not submit:
            _cancel_dry_run(page)
            return SubmissionResult(SubmissionOutcome.DRY_RUN, "reply filled but not submitted")
        try:
            acknowledgements_before = _success_acknowledgements(page)
        except Exception:
            # If the baseline cannot be captured, a later toast may be stale.
            acknowledgements_before = None
        if not _click_submit(page):
            return SubmissionResult(SubmissionOutcome.FAILED, "submit control could not be clicked")
    except Exception as exc:
        return SubmissionResult(SubmissionOutcome.FAILED, f"submission interaction failed: {exc}")

    last_result = SubmissionResult(SubmissionOutcome.AMBIGUOUS, "submit verification did not settle")
    for attempt in range(8):
        if attempt:
            try:
                page.wait_for_timeout(750)
            except Exception:
                pass
        # A success toast is independent positive evidence. TikTok shows it only
        # after its API answers, which can be well after the click, so check it
        # on every attempt rather than once. Under the "Awaiting reply" filter a
        # replied row usually just disappears, so the toast is often the only
        # proof available. Exceptions here are not success.
        if acknowledgements_before is not None:
            try:
                if Counter(_success_acknowledgements(page)) - Counter(acknowledgements_before):
                    return SubmissionResult(SubmissionOutcome.SUBMITTED, positive_signal="success_acknowledgement")
            except Exception:
                pass
        try:
            current = rescan()
        except Exception as exc:
            last_result = SubmissionResult(
                SubmissionOutcome.AMBIGUOUS,
                f"submit clicked but verification failed: {exc}",
            )
            continue
        last_result = _result_from_rescan(card, current)
        if last_result.outcome is SubmissionOutcome.SUBMITTED:
            return last_result
    return last_result


def _result_from_rescan(before: ReviewCard, scan: PageScan) -> SubmissionResult:
    if scan.state not in (PageState.LOADED_WITH_ROWS, PageState.LOADED_EMPTY):
        return SubmissionResult(SubmissionOutcome.AMBIGUOUS, f"verification scan was {scan.state.value}: {scan.detail}")
    matches = [candidate for candidate in scan.cards if candidate.key == before.key]
    if not matches:
        # TikTok injects the submitted response and its date into the row. That
        # can change parsed product/review text (and therefore the content hash)
        # even though this is still the same review. Reconcile by the stable UI
        # identifiers, but continue only when they identify exactly one row.
        matches = [
            candidate for candidate in scan.cards
            if candidate.order_id == before.order_id
            and candidate.product_id == before.product_id
            and (not before.username or candidate.username == before.username)
        ]
    if not matches:
        return SubmissionResult(
            SubmissionOutcome.AMBIGUOUS,
            "target was absent from one verification scan; manual reconciliation is required",
        )
    if len(matches) != 1:
        return SubmissionResult(SubmissionOutcome.AMBIGUOUS, "target identity was not unique after submit")
    after = matches[0]
    if before.has_reply and not after.has_reply:
        return SubmissionResult(SubmissionOutcome.SUBMITTED, positive_signal="reply_action_removed")
    if before.response_count is not None and after.response_count is not None and after.response_count > before.response_count:
        return SubmissionResult(SubmissionOutcome.SUBMITTED, positive_signal="response_count_increased")
    return SubmissionResult(SubmissionOutcome.AMBIGUOUS, "submit clicked but target remained actionable and unchanged")


def _open_editor_for_identity(page, card: ReviewCard) -> bool | dict:
    """True when the editor was opened, else the script's match counts."""
    result = page.evaluate(_OPEN_EDITOR_SCRIPT, {
        "order_id": card.order_id,
        "username": card.username,
        "product_id": card.product_id,
        "product": card.product,
        "review_text": card.review_text,
    })
    return result if isinstance(result, dict) else bool(result)


def _fill_editor(page, text: str) -> bool:
    for selector in (
        "textarea[placeholder*='Share more details']:visible",
        "textarea:visible",
        "[contenteditable='true']:visible",
        "[role='textbox']:visible",
    ):
        try:
            editor = page.locator(selector).last
            editor.wait_for(state="visible", timeout=12000)
            editor.fill(text, timeout=8000)
            return True
        except Exception:
            continue
    return False


def _click_submit(page) -> bool:
    try:
        page.locator("button:has-text('Submit')").last.click(timeout=7000)
        return True
    except Exception:
        return False


def _cancel_dry_run(page) -> None:
    try:
        page.locator("button:has-text('Cancel')").last.click(timeout=2000)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass


def _success_acknowledgements(page) -> tuple[str, ...]:
    value = page.evaluate(_SUCCESS_SCRIPT)
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


_OPEN_EDITOR_SCRIPT = r"""
(target) => {
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const normalized = value => compact(value).toLocaleLowerCase();
  const escaped = value => String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const visible = el => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const actionable = el => {
    if (!visible(el) || el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') return false;
    if (el.matches('button, a, [role="button"], [tabindex]')) return true;
    const style = getComputedStyle(el);
    return style.cursor === 'pointer' || /(?:^|\s)cursor-pointer(?:\s|$)/.test(String(el.className || ''));
  };
  const actions = Array.from(document.querySelectorAll('button, a, [role="button"], [tabindex], div, span'))
    .filter(el => actionable(el) && /^Reply$/i.test(compact(el.innerText || el.textContent || '')));
  const matches = [];
  for (const action of actions) {
    let row = action;
    for (let depth = 0; depth < 40 && row && row !== document.body; depth += 1, row = row.parentElement) {
      const text = compact(row.innerText || row.textContent || '');
      if (!/Order ID:\s*\d+/i.test(text) || !/Product ID:\s*\d+/i.test(text)) continue;
      const orderMatches = text.match(/Order ID:\s*\d+/gi) || [];
      if (orderMatches.length !== 1) continue;
      const orderOk = !target.order_id || new RegExp(`Order ID:\\s*${escaped(target.order_id)}(?:\\D|$)`, 'i').test(text);
      const userOk = !target.username || text.includes(target.username);
      const productIdOk = !target.product_id || new RegExp(`Product ID:\\s*${escaped(target.product_id)}(?:\\D|$)`, 'i').test(text);
      const normalizedText = normalized(text);
      const productOk = !target.product || normalizedText.includes(normalized(target.product));
      const reviewOk = !target.review_text || normalizedText.includes(normalized(target.review_text));
      if (orderOk && userOk && productIdOk && productOk && reviewOk) matches.push(action);
      break;
    }
  }
  // A nested span inherits cursor:pointer from its clickable parent, so one
  // Reply control can match twice. Keep the innermost element; its click
  // bubbles to the parent's handler.
  const unique = [...new Set(matches)].filter(el => !matches.some(other => other !== el && el.contains(other)));
  if (unique.length !== 1) return {clicked: false, reply_controls: actions.length, matching_rows: unique.length};
  unique[0].scrollIntoView({block: 'center', inline: 'center'});
  unique[0].click();
  return true;
}
"""


_SUCCESS_SCRIPT = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  return Array.from(document.querySelectorAll('[role="alert"], [class*="toast"], [class*="message"]'))
    .filter(el => visible(el) && /success|submitted|reply sent/i.test(el.innerText || el.textContent || ''))
    .map(el => {
      const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
      const timestamp = el.getAttribute('data-timestamp') || el.getAttribute('data-created-at') || '';
      return `${text}|${timestamp}`;
    });
}
"""
