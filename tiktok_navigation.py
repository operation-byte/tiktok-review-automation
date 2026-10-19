"""Bounded, fail-closed navigation helpers for TikTok review queues.

The functions in this module deliberately depend only on the small Playwright
``page`` protocol (``goto/evaluate/reload/wait_for_timeout``), which keeps the
queue state machine deterministic and independently testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from review_detection import PageScan, PageState, scan_review_page


class NavigationError(RuntimeError):
    """The requested queue could not be opened within the recovery bound."""


class LoginRequiredError(NavigationError):
    """TikTok redirected the browser to authentication."""


class BrowserClosedError(NavigationError):
    """The Playwright page or browser context is no longer usable."""


@dataclass(frozen=True)
class DashboardQueueState:
    negative: int | None = None
    neutral: int | None = None

    def count_for(self, queue_name: str) -> int | None:
        name = _queue_name(queue_name)
        if name not in {"negative", "neutral"}:
            raise ValueError(f"unsupported review queue: {name}")
        return getattr(self, name)


@dataclass(frozen=True)
class OpenQueueResult:
    queue_name: str
    attempts: int
    empty: bool = False


@dataclass(frozen=True)
class PaginationState:
    current_page: int = 1
    total_pages: int = 1
    has_next: bool = False
    page_size: int | None = None


def _queue_name(queue: object) -> str:
    return str(getattr(queue, "name", queue)).strip().lower()


def _is_login_url(url: str) -> bool:
    lowered = (url or "").lower()
    return any(
        marker in lowered
        for marker in ("/login", "/signin", "/sign-in", "account/login", "/passport/", "/auth/")
    )


def _is_closed_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "closed" in message or "target page" in message or "browser has been" in message


def extract_dashboard_queue_counts(text: str) -> DashboardQueueState:
    """Parse queue counts without assuming a particular thousands separator."""

    def count(label: str) -> int | None:
        number = r"([0-9]+(?:[ \u00a0\u202f.,][0-9]{3})*)"
        match = re.search(rf"\b{label}\s+reviews?\b\D{{0,40}}?{number}", text, re.IGNORECASE)
        return int(re.sub(r"\D", "", match.group(1))) if match else None

    return DashboardQueueState(negative=count("Negative"), neutral=count("Neutral"))


def read_dashboard_queue_state(page) -> DashboardQueueState:
    try:
        _assert_authenticated(page)
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
    except (LoginRequiredError, BrowserClosedError):
        raise
    except Exception as exc:
        if _is_closed_error(exc):
            raise BrowserClosedError(str(exc)) from exc
        raise NavigationError(f"could not read dashboard counts: {exc}") from exc
    return extract_dashboard_queue_counts(str(text or ""))


def _assert_authenticated(page) -> None:
    if _is_login_url(getattr(page, "url", "")):
        raise LoginRequiredError("TikTok login is required")
    try:
        health = page.evaluate(_AUTH_HEALTH_SCRIPT) or {}
    except Exception as exc:
        if _is_closed_error(exc):
            raise BrowserClosedError(str(exc)) from exc
        raise NavigationError(f"could not determine TikTok authentication state: {exc}") from exc
    if health.get("login_required"):
        raise LoginRequiredError(str(health.get("detail") or "TikTok authentication is required"))


def wait_for_settled_scan(
    page,
    profile: str,
    queue_name: str,
    *,
    scanner: Callable[[object, str, str], PageScan] = scan_review_page,
    max_polls: int = 15,
    poll_interval_ms: int = 500,
    stable_observations: int = 2,
) -> PageScan:
    """Poll until a terminal scan repeats; never promote loading to empty."""

    if max_polls < 1 or stable_observations < 1:
        raise ValueError("poll and stability bounds must be positive")
    previous: PageScan | None = None
    repeats = 0
    last = PageScan(PageState.LOADING)
    for poll in range(max_polls):
        _assert_authenticated(page)
        last = scanner(page, profile, queue_name)
        if last.state is PageState.BROWSER_CLOSED:
            raise BrowserClosedError(last.detail or "browser closed while scanning")
        if last.state in {PageState.LOGIN_REQUIRED}:
            raise LoginRequiredError(last.detail or "TikTok login is required")
        if last.state is PageState.DETECTION_MISMATCH:
            return last
        terminal = last.state in {PageState.LOADED_WITH_ROWS, PageState.LOADED_EMPTY}
        repeats = repeats + 1 if terminal and last == previous else (1 if terminal else 0)
        if repeats >= stable_observations:
            return last
        previous = last
        if poll + 1 < max_polls:
            page.wait_for_timeout(poll_interval_ms)
    return PageScan(last.state, last.cards, last.review_like_count, "review results did not settle within polling bound")


def open_review_queue(
    page,
    queue,
    *,
    target_url: str | None,
    max_attempts: int = 2,
    polls_per_attempt: int = 15,
    poll_interval_ms: int = 1000,
) -> OpenQueueResult:
    """Open a dashboard queue with one bounded reload recovery by default."""

    name = _queue_name(queue)
    if name not in {"negative", "neutral"}:
        raise ValueError(f"unsupported review queue: {name}")
    if max_attempts < 1 or polls_per_attempt < 1:
        raise ValueError("navigation bounds must be positive")
    label = "Negative reviews" if name == "negative" else "Neutral reviews"
    # Last observation across all attempts, reported only when the queue fails.
    observed = {"step": "queue_card_not_found", "expected_count": None, "marker": False, "click_error": None}
    for attempt in range(1, max_attempts + 1):
        try:
            if target_url is not None:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
            _assert_authenticated(page)
            for poll in range(polls_per_attempt):
                clicked = page.evaluate(_CLICK_QUEUE_SCRIPT, label)
                expected_count = clicked.get("expected_count") if isinstance(clicked, dict) else None
                marker = clicked.get("marker") if isinstance(clicked, dict) else None
                observed.update(step="queue_card_not_found", expected_count=expected_count, marker=bool(marker), click_error=None)
                if clicked:
                    if marker:
                        try:
                            action = page.locator(f'[data-codex-queue-action="{marker}"]')
                            action.scroll_into_view_if_needed(timeout=15_000)
                            action.click(timeout=15_000)
                        except Exception as exc:
                            # The dashboard periodically rerenders and may
                            # detach the marked node. Re-probe on the next poll.
                            observed.update(step="click_failed", click_error=type(exc).__name__)
                            continue
                    # Empty queues do not always render a Reply now action.
                    # The dashboard count is still authoritative, so finish
                    # this queue without trying to click a missing target.
                    if expected_count == 0:
                        return OpenQueueResult(name, attempt, empty=True)
                    if clicked is True or marker:
                        if _wait_for_dashboard_queue_results(
                            page,
                            label,
                            expected_count=expected_count,
                            max_polls=max(8, polls_per_attempt),
                            poll_interval_ms=poll_interval_ms,
                        ):
                            return OpenQueueResult(name, attempt)
                        observed["step"] = "results_not_settled"
                    else:
                        observed["step"] = "reply_action_missing"
                _assert_authenticated(page)
                if poll + 1 < polls_per_attempt:
                    page.wait_for_timeout(poll_interval_ms)
            if attempt < max_attempts:
                page.reload(wait_until="domcontentloaded", timeout=60_000)
        except (LoginRequiredError, BrowserClosedError):
            raise
        except Exception as exc:
            if _is_closed_error(exc):
                raise BrowserClosedError(str(exc)) from exc
            if attempt >= max_attempts:
                raise NavigationError(f"could not open {name} review queue: {exc}{_queue_failure_detail(page, label, observed)}") from exc
            try:
                page.reload(wait_until="domcontentloaded", timeout=60_000)
            except Exception as reload_exc:
                if _is_closed_error(reload_exc):
                    raise BrowserClosedError(str(reload_exc)) from reload_exc
    raise NavigationError(f"could not open {name} review queue after {max_attempts} attempts{_queue_failure_detail(page, label, observed)}")


def _queue_failure_detail(page, label: str, observed: dict) -> str:
    """Summarize why the queue did not open using only counts and fixed labels."""
    try:
        diagnostic = page.evaluate(_QUEUE_DIAGNOSTIC_SCRIPT, {"label": label})
    except Exception:
        diagnostic = None
    if not isinstance(diagnostic, dict):
        diagnostic = {}
    parts = [
        f"last step={observed['step']}",
        f"expected_count={observed['expected_count']}",
        f"marker={'yes' if observed['marker'] else 'no'}",
        f"click_error={observed['click_error'] or 'none'}",
        f"card_found={'yes' if diagnostic.get('queue_card_found') else 'no'}",
        f"reply_now={diagnostic.get('reply_now_count')}",
        f"ratings_headings={diagnostic.get('ratings_headings') or []}",
        f"rows={diagnostic.get('row_count')}",
        f"stars={diagnostic.get('row_star_counts') or []}",
    ]
    return " (" + " ".join(parts) + ")"


def _wait_for_dashboard_queue_results(
    page, label: str, *, expected_count: int | None, max_polls: int, poll_interval_ms: int
) -> bool:
    """Wait until the queue count and rendered Ratings heading agree."""
    consecutive_matches = 0
    for poll in range(max_polls):
        _assert_authenticated(page)
        if page.evaluate(_QUEUE_RESULTS_SETTLED_SCRIPT, {"label": label, "expected_count": expected_count}):
            consecutive_matches += 1
            if consecutive_matches >= 3:
                return True
        else:
            consecutive_matches = 0
        if poll + 1 < max_polls:
            page.wait_for_timeout(poll_interval_ms)
    return False


def read_pagination(page) -> PaginationState:
    try:
        raw = page.evaluate(_PAGINATION_SCRIPT) or {}
    except Exception as exc:
        if _is_closed_error(exc):
            raise BrowserClosedError(str(exc)) from exc
        raise NavigationError(f"could not inspect review pagination: {exc}") from exc
    current = max(1, int(raw.get("current_page") or 1))
    total = max(current, int(raw.get("total_pages") or current))
    size = raw.get("page_size")
    return PaginationState(current, total, bool(raw.get("has_next", current < total)), int(size) if size else None)


def go_to_next_page(page) -> bool:
    """Click an enabled next-page control, returning False at the last page."""
    try:
        return bool(page.evaluate(_NEXT_PAGE_SCRIPT))
    except Exception as exc:
        if _is_closed_error(exc):
            raise BrowserClosedError(str(exc)) from exc
        raise NavigationError(f"could not advance review pagination: {exc}") from exc


_CLICK_QUEUE_SCRIPT = r"""
(label) => {
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const actionable = el => {
    if (!visible(el)) return false;
    if (el.matches('button, a, [role="button"], [tabindex]')) return true;
    const style = getComputedStyle(el);
    return style.cursor === 'pointer' || /(?:^|\s)cursor-pointer(?:\s|$)/.test(String(el.className || ''));
  };
  const explicitlyActionable = el => {
    if (!visible(el)) return false;
    if (el.matches('button, a, [role="button"], [tabindex]')) return true;
    return /(?:^|\s)cursor-pointer(?:\s|$)/.test(String(el.className || '')) || el.hasAttribute('onclick');
  };
  const queueCount = () => {
    const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    for (const heading of Array.from(document.querySelectorAll('body *')).filter(el => visible(el) && compact(el.textContent) === label)) {
      let card = heading;
      for (let depth = 0; depth < 6 && card; depth += 1, card = card.parentElement) {
        const match = compact(card.textContent).match(new RegExp(`${escaped}\\D{0,20}(\\d[\\d ,.\\u00a0\\u202f]*)`, 'i'));
        if (match) return Number(match[1].replace(/\D/g, ''));
      }
    }
    return null;
  };
  const expectedCount = queueCount();
  // TikTok can hide Reply now when this queue is empty. Return the count so
  // the caller can verify the empty result without requiring a click target.
  if (expectedCount === 0) return {route: 'dashboard', expected_count: 0, marker: null};
  const otherLabel = label === 'Negative reviews' ? 'Neutral reviews' : 'Negative reviews';
  for (const heading of Array.from(document.querySelectorAll('body *')).filter(el => visible(el) && compact(el.textContent) === label)) {
    let card = heading;
    for (let depth = 0; depth < 12 && card; depth += 1, card = card.parentElement) {
      if (compact(card.textContent).includes(otherLabel)) continue;
      const actions = Array.from(card.querySelectorAll('button, a, [role="button"], [tabindex], div, span')).filter(el =>
        explicitlyActionable(el) && /^Reply now(?:\s|›|>|$)/i.test(compact(el.textContent))
      );
      if (actions.length === 1) {
        const marker = label === 'Negative reviews' ? 'negative' : 'neutral';
        for (const stale of document.querySelectorAll('[data-codex-queue-action]')) {
          stale.removeAttribute('data-codex-queue-action');
        }
        actions[0].setAttribute('data-codex-queue-action', marker);
        return {route: 'dashboard', expected_count: expectedCount, marker};
      }
    }
  }
  return false;
}
"""


_CLICK_EXACT_FILTER_SCRIPT = r"""
(request) => {
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const controls = Array.from(document.querySelectorAll('button, [role="button"], [tabindex], div, span'))
    .filter(el => {
      if (!visible(el)) return false;
      if (el.matches('button, [role="button"], [tabindex]')) return true;
      return getComputedStyle(el).cursor === 'pointer' || el.hasAttribute('onclick');
    });
  let matches;
  if (request.kind === 'reset') {
    matches = controls.filter(el => /^Reset$/i.test(compact(el.innerText || el.textContent || '')));
  } else if (request.kind === 'awaiting') {
    matches = controls.filter(el => /^Awaiting reply$/i.test(compact(el.innerText || el.textContent || '')));
  } else {
    const rating = String(request.rating);
    matches = controls.filter(el => {
      const text = compact(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
      return new RegExp(`^${rating}(?:\\s*(?:star|stars|[★☆]))?$`, 'i').test(text);
    });
  }
  matches.sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
  const control = matches[0];
  if (!control) return false;
  control.scrollIntoView({block: 'center'});
  control.click();
  return true;
}
"""


_FILTERS_ACTIVE_SCRIPT = r"""
(request) => {
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const selected = el => {
    if (el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-selected') === 'true') return true;
    if (/(?:active|checked|selected)/i.test(String(el.className || ''))) return true;
    const style = getComputedStyle(el);
    return style.backgroundColor !== 'rgba(0, 0, 0, 0)' && style.backgroundColor !== 'transparent';
  };
  const controls = Array.from(document.querySelectorAll('button, [role="button"], [tabindex], div, span'))
    .filter(el => {
      if (!visible(el)) return false;
      if (el.matches('button, [role="button"], [tabindex]')) return true;
      return getComputedStyle(el).cursor === 'pointer' || el.hasAttribute('onclick');
    });
  const exact = pattern => controls
    .filter(el => pattern.test(compact(el.innerText || el.textContent || el.getAttribute('aria-label') || '')))
    .sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)[0];
  const awaiting = exact(/^Awaiting reply$/i);
  if (!awaiting || !selected(awaiting)) return false;
  return request.ratings.every(value => {
    const rating = exact(new RegExp(`^${value}(?:\\s*(?:star|stars|[★☆]))?$`, 'i'));
    return rating && selected(rating);
  });
}
"""

_QUEUE_RESULTS_SETTLED_SCRIPT = r"""
(request) => {
  const label = request.label;
  const requestedCount = request.expected_count;
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  if (Number.isFinite(requestedCount)) {
    const countSettled = Array.from(document.querySelectorAll('body *')).some(el =>
      visible(el) && new RegExp(`^${requestedCount}\\s+Ratings?$`, 'i').test(compact(el.textContent))
    );
    if (!countSettled) return false;
    if (requestedCount === 0) return true;
    const allowed = label === 'Negative reviews' ? new Set([1, 2]) : new Set([3]);
    const rows = Array.from(document.querySelectorAll('[class*="ratingListItem"], [data-e2e*="rating"], [data-e2e*="review"]'))
      .filter(el => visible(el));
    if (rows.length < Math.min(requestedCount, 50)) return false;
    return rows.every(row => {
      const stars = row.querySelectorAll('[class*="ratingStar"] [class*="activeStar"]').length;
      return allowed.has(stars);
    });
  }
  const headings = Array.from(document.querySelectorAll('body *'))
    .filter(el => visible(el) && compact(el.textContent) === label);
  for (const heading of headings) {
    let card = heading;
    for (let depth = 0; depth < 6 && card; depth += 1, card = card.parentElement) {
      const text = compact(card.textContent);
      const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const match = text.match(new RegExp(`${escaped}\\D{0,20}(\\d[\\d ,.\\u00a0\\u202f]*)`, 'i'));
      if (!match) continue;
      const count = Number(match[1].replace(/\D/g, ''));
      if (!Number.isFinite(count)) continue;
      return Array.from(document.querySelectorAll('body *')).some(el =>
        visible(el) && new RegExp(`^${count}\\s+Ratings?$`, 'i').test(compact(el.textContent))
      );
    }
  }
  return false;
}
"""

_QUEUE_DIAGNOSTIC_SCRIPT = r"""
(request) => {
  const label = request.label;
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const elements = Array.from(document.querySelectorAll('body *')).filter(visible);
  const texts = elements.map(el => compact(el.textContent));
  const rows = Array.from(document.querySelectorAll('[class*="ratingListItem"], [data-e2e*="rating"], [data-e2e*="review"]'))
    .filter(el => visible(el));
  const starCounts = new Set(rows.map(row => row.querySelectorAll('[class*="ratingStar"] [class*="activeStar"]').length));
  const headings = new Set(texts.filter(text => /^\d+\s+Ratings?$/i.test(text)));
  return {
    queue_card_found: texts.some(text => text === label),
    reply_now_count: texts.filter(text => /^Reply now(?:\s|›|>|$)/i.test(text)).length,
    ratings_headings: Array.from(headings).slice(0, 5),
    row_count: rows.length,
    row_star_counts: Array.from(starCounts).sort((a, b) => a - b),
    path: location.pathname,
  };
}
"""

_AUTH_HEALTH_SCRIPT = r"""
() => { // NAV_AUTH_HEALTH
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const body = (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
  const password = Array.from(document.querySelectorAll('input[type="password"]')).some(visible);
  const loginForm = Array.from(document.querySelectorAll('form')).some(form =>
    visible(form) && /(?:log|sign)\s*in|verification code|continue with/i.test(form.innerText || '')
  );
  const challenge = Array.from(document.querySelectorAll('[class*="captcha" i], [id*="captcha" i], [class*="verification" i]')).some(visible);
  const authHeading = /^(?:Log|Sign)\s*in\b|verify (?:your|the) (?:identity|account)|security verification/i.test(body);
  const required = password || loginForm || challenge || authHeading;
  return {login_required: required, detail: required ? 'TikTok login or authentication challenge is visible' : ''};
}
"""

_PAGINATION_SCRIPT = r"""
() => {
  const selected = document.querySelector('[aria-current="page"], [class*="pagination"] [class*="active"]');
  const current = Number((selected && selected.textContent || '').trim()) || 1;
  const pageNumbers = Array.from(document.querySelectorAll('[class*="pagination"] button, [class*="pagination"] li'))
    .map(el => Number((el.textContent || '').trim())).filter(Number.isFinite);
  const next = document.querySelector('[aria-label*="next" i], button[class*="next" i], li[class*="next" i] button');
  const disabled = !next || next.disabled || next.getAttribute('aria-disabled') === 'true' || /disabled/i.test(next.className || '');
  const size = document.querySelector('[class*="page-size" i], [class*="pageSize"]');
  return {current_page: current, total_pages: Math.max(current, ...pageNumbers), has_next: !disabled, page_size: Number((size && size.textContent || '').match(/\d+/)?.[0]) || null};
}
"""

_NEXT_PAGE_SCRIPT = r"""
() => {
  const next = document.querySelector('[aria-label*="next" i], button[class*="next" i], li[class*="next" i] button');
  if (!next || next.disabled || next.getAttribute('aria-disabled') === 'true' || /disabled/i.test(next.className || '')) return false;
  next.scrollIntoView({block: 'center'}); next.click(); return true;
}
"""
