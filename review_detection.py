from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PageState(str, Enum):
    LOADED_WITH_ROWS = "loaded_with_rows"
    LOADED_EMPTY = "loaded_empty"
    LOADING = "loading"
    LOGIN_REQUIRED = "login_required"
    BROWSER_CLOSED = "browser_closed"
    DETECTION_MISMATCH = "detection_mismatch"


@dataclass(frozen=True)
class ReviewCard:
    key: str
    order_id: str
    username: str
    product_id: str
    product: str
    review_text: str
    review_date: str
    rating: int | None
    response_count: int | None
    has_reply: bool
    text: str


@dataclass(frozen=True)
class PageScan:
    state: PageState
    cards: tuple[ReviewCard, ...] = ()
    review_like_count: int = 0
    detail: str = ""


def make_review_key(
    profile: str,
    queue_name: str,
    order_id: str,
    username: str,
    product_id: str,
    product: str,
    review_text: str,
    discriminator: str = "",
) -> str:
    digest_source = "\n".join(
        (product_id.strip(), product.strip(), review_text.strip(), discriminator.strip())
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    return f"{profile}|{queue_name}|{order_id.strip()}|{username.strip()}|{digest}"


def classify_raw_scan(raw: dict[str, Any], profile: str, queue_name: str) -> PageScan:
    if raw.get("login_required"):
        return PageScan(PageState.LOGIN_REQUIRED, detail="TikTok login is required")
    review_like_count = int(raw.get("review_like_count", 0))
    raw_cards = raw.get("cards", [])
    # TikTok's empty-state illustration contains spinner/loading class names.
    # An explicit visible "No search results" marker is authoritative when
    # there are no rows, even if that decorative element looks like a loader.
    if raw.get("empty_marker") and not review_like_count and not raw_cards:
        return PageScan(PageState.LOADED_EMPTY)
    if raw.get("loading") and not review_like_count and not raw_cards:
        return PageScan(PageState.LOADING, detail="review results are still loading")

    parsed_cards: list[ReviewCard] = []
    malformed_count = 0
    seen_keys: set[str] = set()
    for item in raw_cards:
        order_id = str(item.get("order_id", "")).strip()
        product_id = str(item.get("product_id", "")).strip()
        if not order_id or not product_id:
            malformed_count += 1
            continue
        try:
            rating = int(item["rating"]) if item.get("rating") is not None else None
            response_count = int(item["response_count"]) if item.get("response_count") is not None else None
        except (TypeError, ValueError):
            malformed_count += 1
            continue
        key = make_review_key(
            profile,
            queue_name,
            order_id,
            str(item.get("username", "")),
            product_id,
            str(item.get("product", "")),
            str(item.get("review_text", "")),
            str(item.get("discriminator", "")),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        parsed_cards.append(ReviewCard(
            key=key,
            order_id=order_id,
            username=str(item.get("username", "")),
            product_id=product_id,
            product=str(item.get("product", "")),
            review_text=str(item.get("review_text", "")),
            review_date=str(item.get("review_date", "")),
            rating=rating,
            response_count=response_count,
            has_reply=bool(item.get("has_reply")),
            text=str(item.get("text", "")),
        ))
    cards = tuple(parsed_cards)
    ambiguous_count = int(raw.get("ambiguous_identity_count", 0))
    if ambiguous_count:
        return PageScan(
            PageState.DETECTION_MISMATCH,
            cards,
            review_like_count,
            f"{ambiguous_count} review row(s) have indistinguishable identities",
        )
    if malformed_count:
        return PageScan(
            PageState.DETECTION_MISMATCH,
            cards,
            review_like_count,
            f"{malformed_count} visible review row(s) could not be parsed",
        )
    if cards:
        return PageScan(PageState.LOADED_WITH_ROWS, cards, review_like_count)
    if review_like_count:
        return PageScan(PageState.DETECTION_MISMATCH, (), review_like_count, "visible review rows were not parsed")
    if raw.get("empty_marker"):
        return PageScan(PageState.LOADED_EMPTY)
    return PageScan(PageState.LOADING, detail="results have not reached a settled empty state")


def scan_review_page(page, profile: str, queue_name: str) -> PageScan:
    try:
        raw = page.evaluate(_SCAN_SCRIPT)
    except Exception as exc:
        message = str(exc).lower()
        state = PageState.BROWSER_CLOSED if "closed" in message or "target" in message else PageState.DETECTION_MISMATCH
        return PageScan(state, detail=str(exc))
    return classify_raw_scan(raw, profile, queue_name)


_SCAN_SCRIPT = r"""
() => {
  const compact = value => (value || '').replace(/\s+/g, ' ').trim();
  const stableHash = value => {
    let hash = 2166136261;
    for (const char of value) {
      hash ^= char.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, '0');
  };
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const actionable = el => {
    if (!visible(el) || el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') return false;
    if (el.matches('button, a, [role="button"], [tabindex]')) return true;
    const style = getComputedStyle(el);
    return style.cursor === 'pointer' || /(?:^|\s)cursor-pointer(?:\s|$)/.test(String(el.className || ''));
  };
  const bodyText = compact(document.body.innerText || '');
  const loginRequired = /(?:Log|Sign) in(?: to TikTok Shop)?/i.test(bodyText) &&
    Boolean(document.querySelector('input[type="password"], form[action*="login"], [data-e2e*="login"]'));
  const loading = Array.from(document.querySelectorAll('[aria-busy="true"], [class*="loading"], [class*="spinner"]')).some(visible);
  const emptyMarker = /No search results|No reviews|No data/i.test(bodyText);
  const reviewLikeRows = Array.from(document.querySelectorAll('body *')).filter(el => {
    if (!visible(el)) return false;
    const text = compact(el.innerText || el.textContent || '');
    if (!/Order ID:\s*\d+/i.test(text) || !/Product ID:\s*\d+/i.test(text)) return false;
    return !Array.from(el.children).some(child => {
      if (!visible(child)) return false;
      const childText = compact(child.innerText || child.textContent || '');
      return /Order ID:\s*\d+/i.test(childText) && /Product ID:\s*\d+/i.test(childText);
    });
  });
  const nodes = Array.from(document.querySelectorAll('[class*="ratingListItem"], [data-e2e*="rating"], [data-e2e*="review"]'));
  const replyControls = Array.from(document.querySelectorAll('button, a, [role="button"], [tabindex], div, span')).filter(el => {
    if (!actionable(el)) return false;
    return /^Reply(?:\s|$)/i.test(compact(el.innerText || el.textContent || el.getAttribute('aria-label') || ''));
  });
  const candidates = [...nodes, ...replyControls].filter((el, i, all) => visible(el) && all.indexOf(el) === i);
  const rows = [];
  for (const candidate of candidates) {
    let row = candidate;
    while (row && row !== document.body) {
      const text = compact(row.innerText || row.textContent || '');
      const orders = text.match(/Order ID:\s*\d+/gi) || [];
      if (orders.length === 1 && /Product ID:\s*\d+/i.test(text)) break;
      row = row.parentElement;
    }
    if (!row || row === document.body || rows.includes(row)) continue;
    rows.push(row);
  }
  const cards = rows.map(row => {
    const text = compact(row.innerText || row.textContent || '');
    const order = text.match(/Order ID:\s*(\d+)/i);
    const productId = text.match(/Product ID:\s*(\d+)/i);
    const user = text.match(/(@[^\s]+)(?=\s+(?:Reply|Review details))/i) || text.match(/(@[A-Za-z0-9_.-]+)/);
    const date = text.match(/(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})/);
    // TikTok has labelled the count as "0 response(s)" and as "0 replies".
    // Use the last count before the order ID so buyer text cannot spoof it.
    const orderAt = order ? text.indexOf(order[0]) : text.length;
    const counts = Array.from(text.matchAll(/(\d+)\s+(?:response\(s\)|repl(?:y|ies))(?![A-Za-z])/gi))
      .filter(match => match.index < orderAt);
    const response = counts.length ? counts[counts.length - 1] : null;
    const action = Array.from(row.querySelectorAll('button, a, [role="button"], [tabindex], div, span')).find(el =>
      actionable(el) &&
      /^Reply(?:\s|$)/i.test(compact(el.innerText || el.textContent || el.getAttribute('aria-label') || '')) &&
      compact(el.innerText || el.textContent || el.getAttribute('aria-label') || '').length < 40
    );
    const starContainer = row.querySelector('[class*="ratingStar"]');
    const structuralRating = starContainer
      ? starContainer.querySelectorAll('[class*="activeStar"]').length
      : 0;
    const starText = text.match(/^([★☆\s]+)/);
    const textualRating = starText ? (starText[1].match(/★/g) || []).length : 0;
    const rating = structuralRating || textualRating || null;
    let reviewText = '';
    if (date && response) {
      const start = text.indexOf(date[0]) + date[0].length;
      const end = response.index;
      if (end >= start) reviewText = compact(text.slice(start, end));
    }
    let product = '';
    if (productId) {
      const start = text.indexOf(productId[0]) + productId[0].length;
      const end = user ? text.indexOf(user[0], start) : -1;
      product = compact(text.slice(start, end > start ? end : undefined)).slice(0, 180);
    }
    const identityBase = `${order ? order[1] : ''}|${productId ? productId[1] : ''}`;
    const explicitId = row.getAttribute('data-review-id') || row.getAttribute('data-id') || row.id || '';
    const needsFallback = !user || (!reviewText && !product);
    const discriminator = explicitId || (needsFallback ? `${identityBase}|${stableHash(text)}` : '');
    return {
      text, order_id: order ? order[1] : '', username: user ? user[1] : '',
      product_id: productId ? productId[1] : '', product, review_text: reviewText.slice(0, 300),
      review_date: date ? date[0] : '', rating, response_count: response ? Number(response[1]) : null,
      has_reply: Boolean(action), discriminator
    };
  }).filter(card => card.order_id);
  const sparseIdentityCounts = new Map();
  for (const card of cards) {
    if (!card.discriminator) continue;
    const identity = `${card.order_id}|${card.product_id}|${card.username}|${card.product}|${card.review_text}|${card.discriminator}`;
    sparseIdentityCounts.set(identity, (sparseIdentityCounts.get(identity) || 0) + 1);
  }
  const ambiguousIdentityCount = Array.from(sparseIdentityCounts.values())
    .filter(count => count > 1).reduce((total, count) => total + count, 0);
  return {
    login_required: loginRequired, loading, empty_marker: emptyMarker,
    review_like_count: reviewLikeRows.length, ambiguous_identity_count: ambiguousIdentityCount, cards
  };
}
"""
