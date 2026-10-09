import argparse
import csv
import hashlib
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Iterable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from queue_processor import ItemAction, ItemDecision, QueueOutcome, process_paginated_queue
from reply_submission import SubmissionOutcome, submit_reply as submit_review_reply
from review_detection import PageState, ReviewCard, scan_review_page
from tiktok_navigation import (
    LoginRequiredError,
    NavigationError,
    go_to_next_page,
    open_review_queue,
    read_dashboard_queue_state,
    read_pagination,
    wait_for_settled_scan,
)


SELLER_REGION = "SG"
PRODUCT_RATING_BASE_URL = "https://seller-sg.tiktok.com/product/rating"
DEFAULT_GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
DEFAULT_GOOGLE_WORKSHEET_GID = int(os.getenv("GOOGLE_WORKSHEET_GID", "0"))
GOOGLE_SHEET_HEADER = [
    "Order ID",
    "Date of Negative Review",
    "Shop Name",
    "Type of Review",
    "Negative Review Category",
    "Replied on TikTok",
    "Followed up on Duoke",
    "Did Customer Reply",
    "Changed Rating",
]

DEFAULT_REPLY_TEXT = (
    "Hi, we are sorry for the experience. Please contact us via TikTok Shop chat "
    "with your order details so our team can assist you."
)
DEFAULT_NEUTRAL_REPLY_TEXT = (
    "Thank you for sharing your feedback. We are sorry that your experience did not fully meet expectations. Please contact us via chat with your order details so we can look into the issue and assist where possible. We appreciate your support and will continue improving our service."
)

SERIOUS_CONCERN_REPLY_TEXT = (
    "We are sorry to hear about your experience. We take concerns like this "
    "seriously and would like to investigate. Please contact us via TikTok Shop "
    "chat with your order details and any relevant photos or videos so our team "
    "can review what happened and assist."
)


# Reply-routing is intentionally kept in this file so the scheduled auto-reply
# run and any manual-review workflow can use the same decision table.
SELF_HEATING_PRODUCT_MARKERS = (
    "self heating", "self-heating", "chwee song", "heating soup",
)

HIGH_RISK_REVIEW_MARKERS = (
    "food poisoning", "food poison", "made me sick", "fell sick", "hospital",
    "allergic", "allergy", "anaphyl", "mould", "mold", "insect", "bug",
    "foreign object", "glass", "metal", "poison", "counterfeit", "fake",
    "fraud", "scam", "legal action", "police report", "hsa", "case against",
    "made in china", "from japan", "country of origin", "fooled",
    "cheated", "cheat",
)

AUTO_REPLY_TERMINAL_STATUSES = {
    "submitted",
    # Submit was clicked but TikTok gave no positive verification. Never retry
    # automatically: a human must first check whether the reply actually landed.
    "ambiguous",
    "ambiguous_submit_manual_check",
    "needs_manual_review_missing_date",
    "needs_manual_review_high_risk",
}

REPLY_TEMPLATES = {
    "Product Usability / Instructions": (
        "We are sorry to hear that. Please contact us via TikTok Shop chat with "
        "your order details so our team can guide you and assist further."
    ),
    "Damaged / Packaging": (
        "We are sorry your item arrived in this condition. Please contact us via "
        "TikTok Shop chat with your order details and clear photos or videos of "
        "the item and outer packaging so we can check and assist."
    ),
    "Missing / Wrong Item": (
        "We are sorry about this. Please contact us via TikTok Shop chat with "
        "your order details and a photo of the items received so we can check "
        "and assist."
    ),
    "Delivery / Fulfilment": (
        "We are sorry to hear about the delivery experience. Please contact us "
        "via TikTok Shop chat with your order details and any relevant photos so "
        "we can look into this and assist."
    ),
    "Expiry / Date Concern": (
        "We are sorry for the concern. Please contact us via TikTok Shop chat "
        "with your order details and a clear photo of the date marking and "
        "packaging so we can verify and assist."
    ),
    "Taste / Product Quality": (
        "We are sorry the product did not meet your expectations. We understand "
        "taste can be subjective, and we appreciate your feedback. Please contact "
        "us via TikTok Shop chat with your order details if there is a product "
        "quality concern for us to check."
    ),
    "Customer Service": (
        "We sincerely apologise for the experience. Please contact us via TikTok "
        "Shop chat with your order details so our team can review this and assist."
    ),
    "Price / Value": (
        "Thank you for your feedback. We are sorry the product did not meet your "
        "expectations on value, and we will share this with the team for review."
    ),
    "Other / Needs Review": DEFAULT_REPLY_TEXT,
}

SELF_HEATING_USAGE_REPLY_TEXT = (
    "We are sorry to hear this. For the self-heating soup, please pull the red "
    "ribbon or string firmly and fully in one smooth motion to activate the "
    "heating pack. If it still does not heat properly after about 5 minutes, "
    "please contact us via TikTok Shop chat with your order details so we can assist."
)

# This cloud version uses isolated browser profiles under a persistent Docker volume.
_PROFILE_ROOT = Path(os.getenv("PROFILE_ROOT", "browser_profiles"))
PROFILES = {
    key: {"label": label, "user_data_dir": str(_PROFILE_ROOT / key)}
    for key, label in {
        "welive_main": "WELIVE Supermarket SG",
        "snackersbox": "Snacker's Box",
        "konomi_living": "Konomi Living",
    }.items()
}
PROFILE_RUN_ORDER = ["welive_main", "snackersbox", "konomi_living"]
LOGIN_REQUIRED_PROFILES_PATH = Path("alerts/login_required_profiles.txt")
PROFILE_LAUNCH_FAILED_PROFILES_PATH = Path("alerts/profile_launch_failed_profiles.txt")
# Real Chrome under Playwright will not flush its cookie store to disk, so we
# persist each profile's login cookies here and re-inject them on every run.
COOKIE_JAR_DIR = Path("alerts/cookie_jars")
LOGIN_HELPER_PORT = 47653
LOGIN_HELPER_PID_PATH = Path("alerts/login_helper.pid")
# A login helper older than this is treated as abandoned and reclaimed, so one
# hung run can no longer wedge every future login attempt.
LOGIN_HELPER_STALE_SECONDS = 20 * 60


@dataclass(frozen=True)
class ReviewQueue:
    name: str
    rating_filter: tuple[str, ...]
    default_reply: str


REVIEW_QUEUES = {
    "negative": ReviewQueue("negative", ("1", "2"), DEFAULT_REPLY_TEXT),
    "neutral": ReviewQueue("neutral", ("3",), DEFAULT_NEUTRAL_REPLY_TEXT),
}


def is_expected_queue_rating(card: ReviewCard, queue: ReviewQueue) -> bool:
    """Reject a known contradictory rating after authoritative queue entry."""
    if card.rating is None:
        return True
    return str(card.rating) in queue.rating_filter


@dataclass
class ReplyLogEntry:
    replied_at: str
    profile: str
    queue: str
    rating_filter: str
    order_id: str
    username: str
    product: str
    review_text: str
    reply_text: str
    status: str
    review_key: str


LOG_FIELDS = list(ReplyLogEntry.__dataclass_fields__)


MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compact_text(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[:limit]


def parse_review_date(raw_text: str) -> str:
    match = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", raw_text or "")
    if not match:
        return ""
    day = int(match.group(1))
    month = MONTHS.get(match.group(2)[:3].lower())
    year = int(match.group(3))
    if not month:
        return ""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse_iso_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def is_recent_review(
    review_date: str,
    *,
    today: date | None = None,
    recent_days: int = 30,
    since_date: date | None = None,
) -> bool:
    parsed = parse_iso_date(review_date)
    if not parsed:
        return False
    if since_date is not None:
        return parsed >= since_date
    if recent_days <= 0:
        return True
    today = today or datetime.now().astimezone().date()
    return parsed >= today - timedelta(days=recent_days)


def classify_review_date(review_date: str, *, recent_days: int, since_date: date | None) -> str:
    if not parse_iso_date(review_date):
        return "missing"
    return "eligible" if is_recent_review(review_date, recent_days=recent_days, since_date=since_date) else "old"


def review_date_action(review_date: str, *, recent_days: int, since_date: date | None) -> ItemAction | None:
    policy = classify_review_date(review_date, recent_days=recent_days, since_date=since_date)
    if policy == "missing":
        return ItemAction.HOLD_MANUAL
    if policy == "old":
        return ItemAction.SKIP_OLD
    return None


def clean_review_text(value: str) -> str:
    """Remove TikTok UI metadata so only the buyer's written review is routed."""
    text = compact_text(value, 1000)
    if not text:
        return ""

    # A blank review is rendered as UI metadata beginning with "0 response(s)".
    # Treat that as empty rather than replying to the page chrome.
    if re.match(r"^\d+\s+response\(s\)\s+Order ID:", text, flags=re.IGNORECASE):
        return ""
    if (
        "order id:" in text.lower()
        and "product id:" in text.lower()
        and re.search(r"\breply\b", text, flags=re.IGNORECASE)
        and re.search(r"\breview details?\b", text, flags=re.IGNORECASE)
    ):
        return ""

    return compact_text(text, 300)


def extract_review_summary(raw_text: str) -> dict[str, str]:
    text = compact_text(raw_text, 2000)
    order_match = re.search(r"Order ID:\s*(\d+)", text, flags=re.IGNORECASE)
    product_match = re.search(
        r"Product ID:\s*\d+\s*(.*?)(?:\s+@\S+\s+Reply\b|\s+Reply\b|\s+Review details?\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    username_match = re.search(r"(@[A-Za-z0-9_.-]+)", text)

    review_match = re.search(
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b\s*(.*?)"
        r"(?=\s+\d+\s+response\(s\)\s+Order ID:|$)",
        text,
        flags=re.IGNORECASE,
    )
    review_text = clean_review_text(review_match.group(1) if review_match else "")

    return {
        "review_date": parse_review_date(text),
        "order_id": order_match.group(1) if order_match else "",
        "username": username_match.group(1) if username_match else "",
        "product": compact_text(product_match.group(1), 180) if product_match else "",
        "review_text": review_text,
    }


def make_review_key(profile: str, queue_name: str, summary: dict[str, str]) -> str:
    order_id = summary.get("order_id", "").strip()
    username = summary.get("username", "").strip()
    key_text = "\n".join(
        [
            summary.get("product", "").strip(),
            summary.get("review_text", "").strip(),
        ]
    )
    content_digest = hashlib.sha256(key_text.encode("utf-8")).hexdigest()[:12]
    if order_id or username:
        return f"{profile}|{queue_name}|{order_id}|{username}|{content_digest}"
    return f"{profile}|{queue_name}|hash:{content_digest}"


def stable_review_key(review_key: str) -> str:
    parts = (review_key or "").split("|")
    if len(parts) >= 5 and not parts[2].startswith("hash:"):
        return "|".join(parts[:4])
    return review_key or ""


def upgrade_legacy_review_key(row: dict[str, str]) -> str:
    review_key = row.get("review_key", "")
    parts = review_key.split("|")
    if len(parts) != 4:
        return review_key
    profile, queue_name, order_id, username = parts
    return make_review_key(
        profile,
        queue_name,
        {
            "order_id": order_id,
            "username": username,
            "product": row.get("product", ""),
            "review_text": row.get("review_text", ""),
        },
    )


def append_reply_log(path: Path, entry: ReplyLogEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(asdict(entry))


def env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def profile_label(profile: str) -> str:
    return PROFILES.get(profile, {}).get("label", profile)


def classify_review_category(review_text: str, product: str = "") -> str:
    """Classify the buyer's text once, then reuse it for Sheet and reply routing."""
    review = re.sub(r"\s+", " ", (review_text or "").lower()).strip()
    usability_keywords = (
        "hard to pull", "difficult to pull", "cannot pull", "can't pull",
        "cant pull", "pull tab", "pull string", "pulling the string",
        "heating tab", "doesn't heat", "doesnt heat", "does not heat",
        "not heat", "cannot heat", "heat up", "hard to open",
        "difficult to open", "cannot open", "can't open", "cant open",
        "instruction", "instructions", "how to use",
    )
    if any(keyword in review for keyword in usability_keywords):
        return "Product Usability / Instructions"

    # The order matters. Specific operational complaints should win over generic
    # words such as "bad", "small", or "quality".
    rules = [
        (
            "Missing / Wrong Item",
            (
                "missing", "wrong item", "wrong product", "incorrect item",
                "didn't receive", "did not receive", "not received",
                "short of", "short item", "less item", "missing item",
            ),
        ),
        (
            "Damaged / Packaging",
            (
                "damage", "damaged", "defect", "defected", "defective", "broken",
                "burst", "leak", "leaked", "leaking", "dent", "dented",
                "crushed", "spill", "spilled", "packaging", "packet torn",
                "box torn", "arrived open",
            ),
        ),
        (
            "Expiry / Date Concern",
            (
                "expiry", "expired", "expire", "near expiry", "best before",
                "manufacturing date", "mfg", "shelf life", "date marking",
            ),
        ),
        (
            "Delivery / Fulfilment",
            (
                "delivery", "delivered late", "received late", "delay", "delayed",
                "slow shipping", "slow delivery", "courier", "spx", "shipment",
                "shipping", "parcel",
            ),
        ),
        (
            "Product Usability / Instructions",
            (
                "hard to pull", "difficult to pull", "cannot pull", "can't pull",
                "cant pull", "pull tab", "pull string", "pulling the string",
                "heating tab", "doesn't heat", "doesn’t heat", "doesnt heat",
                "does not heat", "not heat", "cannot heat", "heat up",
                "hard to open", "difficult to open", "cannot open", "can't open",
                "cant open", "instruction", "instructions", "how to use",
            ),
        ),
        (
            "Customer Service",
            (
                "customer service", "seller response", "seller reply", "no response",
                "did not reply", "didn't reply", "rude", "attitude", "support",
                "help from seller",
            ),
        ),
        (
            "Price / Value",
            (
                "too expensive", "expensive", "not worth", "overpriced",
                "price too high", "small portion", "portion too small",
            ),
        ),
        (
            "Taste / Product Quality",
            (
                "taste", "tastes", "tasted", "flavour", "flavor", "bland", "salty",
                "sweet", "sour", "bitter", "spicy", "not nice", "stale", "rancid",
                "quality", "disappointed", "not fresh", "not to my liking",
                "doesn't taste", "doesnt taste",
            ),
        ),
    ]

    for category, keywords in rules:
        if any(keyword in review for keyword in keywords):
            return category
    return "Other / Needs Review"


@dataclass(frozen=True)
class ReplyDecision:
    reply_text: str
    category: str
    template_name: str
    auto_submit: bool
    status: str


def is_self_heating_product(product: str) -> bool:
    product_text = (product or "").lower()
    return any(marker in product_text for marker in SELF_HEATING_PRODUCT_MARKERS)


def has_high_risk_review_marker(review_text: str) -> bool:
    review = (review_text or "").lower()
    return any(marker in review for marker in HIGH_RISK_REVIEW_MARKERS)


def select_reply_decision(queue: ReviewQueue, summary: dict[str, str], args) -> ReplyDecision:
    """Choose the same category-based response for every review, before opening TikTok's dialog."""
    review_text = clean_review_text(summary.get("review_text", ""))
    product = summary.get("product", "")

    if not review_text:
        reply_text = args.neutral_reply_text if queue.name == "neutral" else args.negative_reply_text
        return ReplyDecision(
            reply_text=reply_text,
            category="Other / No Review Text",
            template_name=f"no_text_{queue.name}_fallback",
            auto_submit=True,
            status="ready",
        )

    if has_high_risk_review_marker(review_text):
        return ReplyDecision(
            reply_text=SERIOUS_CONCERN_REPLY_TEXT,
            category="Serious Concern / Escalation",
            template_name="serious_concern",
            auto_submit=os.getenv("AUTO_SUBMIT_HIGH_RISK", "false").lower() == "true",
            status="needs_manual_review_high_risk",
        )

    category = classify_review_category(review_text, product)

    # Kept as an escape hatch for a deliberately generic campaign response.
    if args.reply_mode == "generic":
        reply_text = (
            args.neutral_reply_text
            if queue.name == "neutral"
            else args.negative_reply_text
        )
        return ReplyDecision(
            reply_text=reply_text,
            category=category,
            template_name=f"generic_{queue.name}",
            auto_submit=True,
            status="ready",
        )

    if category == "Product Usability / Instructions" and is_self_heating_product(product):
        return ReplyDecision(
            reply_text=SELF_HEATING_USAGE_REPLY_TEXT,
            category=category,
            template_name="self_heating_usage",
            auto_submit=True,
            status="ready",
        )

    # A neutral rating with no recognised complaint remains a simple thank-you.
    if queue.name == "neutral" and category == "Other / Needs Review":
        return ReplyDecision(
            reply_text=args.neutral_reply_text,
            category=category,
            template_name="neutral_default",
            auto_submit=True,
            status="ready",
        )

    reply_text = REPLY_TEMPLATES.get(category)
    if not reply_text:
        reply_text = args.neutral_reply_text if queue.name == "neutral" else args.negative_reply_text

    return ReplyDecision(
        reply_text=reply_text,
        category=category,
        template_name=category.lower().replace(" / ", "_").replace(" ", "_"),
        auto_submit=True,
        status="ready",
    )


def google_review_type(entry: ReplyLogEntry) -> str:
    if entry.queue == "neutral":
        return "Neutral / 3-star review"
    if entry.queue == "negative":
        return "Negative / 1-2 star review"
    return entry.queue.title()


def google_review_category(entry: ReplyLogEntry) -> str:
    return classify_review_category(entry.review_text, entry.product)


def build_google_sheet_row(entry: ReplyLogEntry, review_date: str) -> list[str]:
    replied_value = ""
    if entry.status == "submitted":
        replied_value = f"Yes"
    elif entry.status:
        replied_value = f"No"
    return [
        entry.order_id,
        review_date or entry.replied_at[:10],
        profile_label(entry.profile),
        google_review_type(entry),
        google_review_category(entry),
        replied_value,
        "",  # Followed up on Duoke: the bot cannot know this.
        "",  # Did Customer Reply: leave to the team.
        "",  # Changed Rating: leave to the team.
        "",  # IC: never invent an assignee.
        "",  # Message Template: leave to the team.
        "",  # Follow Up: leave to the team.
        "",  # Status: leave to the team.
    ]


def record_review_date(review_key: str, review_date: str, path: Path = Path("logs/review_dates.csv")) -> None:
    """Store the actual review date separately, without changing the old CSV format."""
    if not review_key or not review_date:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if fresh:
            writer.writerow(["review_key", "review_date"])
        writer.writerow([review_key, review_date])


def load_sheet_logged_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def mark_sheet_logged(path: Path, review_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(review_key + "\n")


def tsv_escape(value: str) -> str:
    # Google Sheets accepts pasted tab-separated values. Keep each value as one cell.
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def google_sheet_csv_export_url(sheet_id: str = DEFAULT_GOOGLE_SHEET_ID, gid: int = DEFAULT_GOOGLE_WORKSHEET_GID) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def google_sheet_url_with_range(url: str, target_cell: str) -> str:
    base = url.split("#", 1)[0].split("&range=", 1)[0]
    gid_match = re.search(r"(?:[?#&]|^)gid=(\d+)", url)
    gid = gid_match.group(1) if gid_match else str(DEFAULT_GOOGLE_WORKSHEET_GID)
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}range={target_cell}#gid={gid}&range={target_cell}"


def fetch_google_sheet_rows(sheet_id: str = DEFAULT_GOOGLE_SHEET_ID, gid: int = DEFAULT_GOOGLE_WORKSHEET_GID, *, request_context=None) -> list[list[str]]:
    """Read Sheet CSV using the signed-in browser when available."""
    url = google_sheet_csv_export_url(sheet_id, gid)
    if request_context is not None:
        response = request_context.get(url, timeout=30000)
        if not response.ok:
            raise RuntimeError("Signed-in browser could not read Sheet CSV")
        raw = response.text().lstrip("\ufeff")
        rows = list(csv.reader(io.StringIO(raw)))
        if not rows or (rows[0][0] if rows[0] else "").strip() != "Order ID":
            raise RuntimeError("Sheet CSV headers invalid; verify Google browser login and Sheet access")
        return rows
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8-sig", errors="replace")
    return list(csv.reader(io.StringIO(raw)))


def google_sheet_row_has_real_data(row: list[str], max_cols: int = 9) -> bool:
    """Only consider actual values in KPI columns A:I, not formatting or old used-range rows."""
    return any((cell or "").strip() for cell in row[:max_cols])


def detect_google_sheet_next_blank_row(*, request_context=None) -> int:
    """Return the next true blank row. Never returns row 1 because row 1 is headers."""
    rows = fetch_google_sheet_rows(request_context=request_context)
    last_data_row = 1
    for index, row in enumerate(rows, start=1):
        if google_sheet_row_has_real_data(row):
            last_data_row = index
    return max(last_data_row + 1, 2)


def google_sheet_contains_order_id(order_id: str, *, request_context=None) -> bool:
    order_id = (order_id or "").strip()
    if not order_id:
        return False
    return any(row and (row[0] or "").strip() == order_id for row in fetch_google_sheet_rows(request_context=request_context))


def google_sheet_row_matches(target_row: int, expected_row: list[str], *, request_context=None) -> bool:
    if target_row < 2 or not expected_row:
        return False
    rows = fetch_google_sheet_rows(request_context=request_context)
    index = target_row - 1
    actual = rows[index] if index < len(rows) else []
    expected_values = [tsv_escape(value) for value in expected_row[:6]]
    actual_values = [tsv_escape(value) for value in actual[:6]]
    if len(actual_values) > 1 and len(expected_values) > 1:
        actual_date = parse_iso_date(actual_values[1])
        expected_iso = parse_review_date(expected_values[1])
        expected_date = parse_iso_date(expected_iso or expected_values[1])
        if actual_date and expected_date and actual_date == expected_date:
            actual_values[1] = expected_values[1]
    if actual_values != expected_values:
        print(
            f"Google Sheet verification mismatch at row {target_row}: "
            f"expected {expected_values}, got {actual_values}"
        )
        return False
    return True


def google_sheet_selection_state(page) -> dict[str, str]:
    try:
        return page.evaluate(
            """() => {
                const active = document.activeElement;
                const selectors = [
                    "#t-name-box",
                    "[aria-label='Name box']",
                    "[aria-label*='Name box']",
                    "input[aria-label*='name box' i]"
                ];
                let nameBox = "";
                for (const selector of selectors) {
                    const element = document.querySelector(selector);
                    if (element && (element.value || element.textContent)) {
                        nameBox = element.value || element.textContent || "";
                        break;
                    }
                }
                return {
                    name_box: nameBox,
                    active_element_label: active ? active.getAttribute("aria-label") || "" : "",
                    active_element_class: active ? active.getAttribute("class") || "" : "",
                    active_element_role: active ? active.getAttribute("role") || "" : "",
                };
            }"""
        )
    except Exception:
        return {}


def google_sheet_selection_matches(state: dict[str, str], target_cell: str) -> bool:
    target = target_cell.upper()
    name_box = (state.get("name_box") or "").strip().upper().replace("$", "")
    active_label = (state.get("active_element_label") or "").strip().upper().replace("$", "")
    if name_box:
        return name_box == target
    if active_label:
        return target in active_label
    return False


def select_google_sheet_cell_by_url(sheet_page, sheet_url: str, cell: str, args) -> bool:
    sheet_page.goto(sheet_url, wait_until="domcontentloaded", timeout=90000)
    sheet_page.wait_for_timeout(args.google_sheet_load_wait_ms)
    state = google_sheet_selection_state(sheet_page)
    if google_sheet_selection_matches(state, cell):
        print(f"Selected Google Sheet cell by URL: {cell}")
        return True
    print(f"Google Sheet selection after URL range {cell}: {state}")
    return not (state.get("name_box") or "").strip()


def select_google_sheet_cell_with_name_box(sheet_page, cell: str, args) -> bool:
    """Select an exact cell using Google Sheets' name box.

    This avoids Ctrl+End, Ctrl+F, Ctrl+G, and URL range focus issues.
    Prefer the actual Sheets name-box element, then fall back to coordinates.
    """
    sheet_page.keyboard.press("Escape")
    sheet_page.wait_for_timeout(700)
    selectors = [
        '[aria-label="Name box (Ctrl + J)"] input',
        'input[aria-label="Name box (Ctrl + J)"]',
        'div[aria-label="Name box (Ctrl + J)"] input',
    ]
    for selector in selectors:
        try:
            locator = sheet_page.locator(selector)
            if locator.count() < 1:
                continue
            name_box = locator.first()
            name_box.click(timeout=2000)
            sheet_page.wait_for_timeout(200)
            name_box.fill(cell, timeout=2000)
            sheet_page.wait_for_timeout(200)
            name_box.press("Enter", timeout=2000)
            sheet_page.wait_for_timeout(1200)
            sheet_page.keyboard.press("Escape")
            sheet_page.wait_for_timeout(300)
            return True
        except Exception:
            continue
    sheet_page.mouse.click(args.google_sheet_click_x, args.google_sheet_click_y)
    sheet_page.wait_for_timeout(500)
    sheet_page.keyboard.press("Control+A")
    sheet_page.wait_for_timeout(200)
    sheet_page.keyboard.insert_text(cell)
    sheet_page.wait_for_timeout(200)
    sheet_page.keyboard.press("Enter")
    sheet_page.wait_for_timeout(1200)
    return True


def paste_google_sheet_row(sheet_page, target_row: int, row: list[str], args, *, sheet_url: str = "") -> None:
    """Write one row with the same type-and-tab method as TikTok Automation."""
    sheet_page.bring_to_front()
    sheet_page.wait_for_timeout(500)
    target_cell = f"A{target_row}"
    if sheet_url:
        selected = select_google_sheet_cell_by_url(sheet_page, sheet_url, target_cell, args)
    else:
        selected = False
    if not selected and not select_google_sheet_cell_with_name_box(sheet_page, target_cell, args):
        raise RuntimeError(f"Could not select Google Sheet cell {target_cell}")

    state = google_sheet_selection_state(sheet_page)
    if not google_sheet_selection_matches(state, target_cell):
        raise RuntimeError(f"Refusing to type: expected active cell {target_cell}, got {state}")

    max_index = 0
    for index, value in enumerate(row):
        if tsv_escape(value):
            max_index = index
    for index in range(max_index + 1):
        value = tsv_escape(row[index] if index < len(row) else "")
        sheet_page.keyboard.type(value)
        sheet_page.wait_for_timeout(400)
        if index < max_index:
            sheet_page.keyboard.press("Tab")
            sheet_page.wait_for_timeout(400)
    sheet_page.keyboard.press("Enter")
    sheet_page.wait_for_timeout(400)


def manual_append_google_sheet_log(page, args, entry: ReplyLogEntry, review_date: str) -> None:
    """Open Google Sheets and paste one KPI row into the real next blank row.

    This uses the same working method as the old-reply sync:
    1. Read Sheet CSV export to find the real last data row in columns A:I.
    2. Force target row to at least A2.
    3. Open Google Sheet.
    4. Select exact target cell using the Name Box.
    5. Paste TSV from column A onward.
    """
    if not args.google_sheets:
        return
    if entry.status != "submitted" and not args.google_log_attempts:
        return

    marker_path = Path(args.google_sheet_marker_path)
    already_logged = load_sheet_logged_keys(marker_path)
    if entry.review_key in already_logged:
        print(
            "Google Sheet manual logging skipped duplicate from local marker: "
            f"order={entry.order_id or 'unknown'} shop={profile_label(entry.profile)}"
        )
        return
    row = build_google_sheet_row(entry, review_date)
    sheet_page = None

    try:
        target_row = detect_google_sheet_next_blank_row(request_context=page.context.request)
        target_cell = f"A{target_row}"
        print(f"Google Sheet target cell detected: {target_cell}")

        context = page.context
        sheet_page = context.new_page()
        sheet_url = google_sheet_url_with_range(args.google_sheet_url, target_cell)
        paste_google_sheet_row(sheet_page, target_row, row, args, sheet_url=sheet_url)
        sheet_page.wait_for_timeout(args.google_sheet_after_paste_wait_ms)
        if not google_sheet_row_matches(target_row, row, request_context=context.request):
            print(
                "Google Sheet manual append could not be verified; "
                "local marker was not written so the bot can retry later: "
                f"order={entry.order_id or 'unknown'} shop={profile_label(entry.profile)}"
            )
            return
        mark_sheet_logged(marker_path, entry.review_key)
        print(
            "Google Sheet manual logged: "
            f"order={entry.order_id or 'unknown'} shop={profile_label(entry.profile)} "
            f"at {target_cell}"
        )
    except Exception as exc:
        print(f"Google Sheet manual append failed but local CSV was saved: {exc}")
    finally:
        if sheet_page is not None:
            try:
                sheet_page.close()
            except Exception:
                pass


class LoggedReviewState(str, Enum):
    SUBMITTED = "submitted"
    HOLD_MANUAL = "hold_manual"
    AMBIGUOUS_MANUAL_CHECK = "ambiguous_manual_check"


def load_logged_review_states(path: Path) -> dict[str, LoggedReviewState]:
    """Return reviews that must not be re-attempted by the auto-reply run.

    Technical failures and former high-risk holds remain retryable. Reviews with
    missing dates are logged once and held for a human instead of being retried.
    """
    if not path.exists():
        return {}
    states: dict[str, LoggedReviewState] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            key, status = row.get("review_key"), row.get("status")
            if not key or status not in AUTO_REPLY_TERMINAL_STATUSES:
                continue
            if status == "submitted":
                state = LoggedReviewState.SUBMITTED
            elif status in {"ambiguous", "ambiguous_submit_manual_check"}:
                state = LoggedReviewState.AMBIGUOUS_MANUAL_CHECK
            else:
                state = LoggedReviewState.HOLD_MANUAL
            states[key] = state
            states[upgrade_legacy_review_key(row)] = state
    return states


def load_logged_keys(path: Path) -> set[str]:
    """Compatibility view of terminal identities; prefer status-aware loading."""
    return set(load_logged_review_states(path))


def iter_log_rows(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_duoke_report(log_path: Path, report_path: Path, date_prefix: str | None = None) -> None:
    date_prefix = date_prefix or datetime.now().astimezone().date().isoformat()
    rows = [
        row
        for row in iter_log_rows(log_path)
        if row.get("replied_at", "").startswith(date_prefix) and row.get("status") == "submitted"
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# TikTok Reviews To Follow Up In Duoke",
        "",
        f"Date: {date_prefix}",
        f"Total submitted TikTok replies: {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("No submitted TikTok review replies logged for this date.")
    for row in rows:
        lines.extend(
            [
                f"## {row.get('profile', '')} - {row.get('queue', '')}",
                f"- Order ID: {row.get('order_id', '') or 'unknown'}",
                f"- Username: {row.get('username', '') or 'unknown'}",
                f"- Product: {row.get('product', '') or 'unknown'}",
                f"- Review: {row.get('review_text', '')}",
                f"- TikTok reply used: {row.get('reply_text', '')}",
                "- Duoke action: reply/follow up with this customer",
                "",
            ]
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_alert_summary(
    log_path: Path,
    alert_path: Path,
    *,
    date_prefix: str | None = None,
    exit_code: int = 0,
    report_path: Path | None = None,
) -> str:
    date_prefix = date_prefix or datetime.now().astimezone().date().isoformat()
    report_path = report_path or Path("reports").joinpath(f"duoke_{date_prefix}.md")
    rows = [
        row
        for row in iter_log_rows(log_path)
        if row.get("replied_at", "").startswith(date_prefix) and row.get("status") == "submitted"
    ]
    login_required_profiles = read_login_required_profiles()
    profile_launch_failed_profiles = read_profile_launch_failed_profiles()
    alert_path.parent.mkdir(parents=True, exist_ok=True)

    if exit_code:
        lines = [
            f"TikTok review bot FAILED with exit code {exit_code}.",
            f"Date: {date_prefix}",
            "Check logs\\daily_auto_reply_run.log for details.",
        ]
        if login_required_profiles:
            labels = ", ".join(profile_label(profile) for profile in login_required_profiles)
            lines.append(f"Login expired for: {labels}")
            lines.append("A login helper window should open automatically. Finish login there, then rerun the bot.")
        if profile_launch_failed_profiles:
            labels = ", ".join(profile_label(profile) for profile in profile_launch_failed_profiles)
            lines.append(f"Chrome profile locked or failed for: {labels}")
            lines.append("Close any automation Chrome windows for those shops, then rerun the bot.")
        lines.append(f"Duoke report: {report_path}")
    elif rows:
        lines = [
            f"TikTok review bot replied to {len(rows)} review(s).",
            f"Date: {date_prefix}",
            f"Duoke follow-up report: {report_path}",
            "",
            "Follow up in Duoke:",
        ]
        for row in rows[:10]:
            lines.append(
                "- "
                f"{row.get('profile', '')} {row.get('queue', '')} | "
                f"Order {row.get('order_id', '') or 'unknown'} | "
                f"{row.get('username', '') or 'unknown'} | "
                f"{compact_text(row.get('review_text', ''), 90)}"
            )
        if len(rows) > 10:
            lines.append(f"- Plus {len(rows) - 10} more in the Duoke report.")
    else:
        lines = [
            "TikTok review bot ran successfully.",
            f"Date: {date_prefix}",
            "No recent negative/neutral review replies were submitted.",
            f"Duoke report: {report_path}",
        ]

    message = "\n".join(lines)
    alert_path.write_text(message + "\n", encoding="utf-8")
    return message


def profile_sequence(profile: str) -> list[str]:
    if profile == "all":
        return list(PROFILE_RUN_ORDER)
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile}. Use all or one of: {', '.join(PROFILES)}")
    return [profile]


def is_login_page(page) -> bool:
    return "/account/login" in (page.url or "").lower()


def cookie_jar_path(profile: str) -> Path:
    return COOKIE_JAR_DIR / f"{profile}.json"


def save_cookies(context, profile: str) -> None:
    """Save cookies *and* localStorage where available; legacy cookie lists remain readable.

    GitHub's runner is discarded after every run. Playwright storage_state can
    preserve additional first-party auth data that a cookies-only export misses.
    Browser fingerprints and server-side session expiry remain outside our control.
    """
    try:
        snapshot = context.storage_state()
        if not isinstance(snapshot, dict) or not snapshot.get("cookies"):
            # Some contexts cannot export storage state; fall back to cookies.
            snapshot = {"cookies": context.cookies(), "origins": []}
    except Exception as exc:
        print(f"[{profile}] Could not read browser storage: {type(exc).__name__}")
        return
    if not snapshot.get("cookies"):
        print(f"[{profile}] No cookies to save; leaving existing session untouched.")
        return
    path = cookie_jar_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot), encoding="utf-8")
    if os.name != "nt":
        tmp.chmod(0o600)
    tmp.replace(path)
    print(f"[{profile}] Saved browser session (cookies + supported local storage).")


def load_cookies_into(context, profile: str) -> bool:
    """Restore a legacy cookie list or a new Playwright storage-state snapshot.

    Persistent browser contexts don't accept the `storage_state` launch option,
    so preload localStorage with an origin-checked initialization script before
    navigating to Seller Center. This never attempts MFA bypass.
    """
    path = cookie_jar_path(profile)
    if not path.exists():
        print(f"[{profile}] No saved session found; run --mode login first.")
        return False
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            cookies, origins = loaded, []
        elif isinstance(loaded, dict):
            cookies = loaded.get("cookies", [])
            origins = loaded.get("origins", [])
        else:
            raise ValueError("Unexpected session format")
        if not isinstance(cookies, list) or not cookies:
            return False
        if not isinstance(origins, list):
            raise ValueError("Invalid origin storage")
        context.add_cookies(cookies)
        safe_origins = {}
        for origin in origins:
            if not isinstance(origin, dict) or not isinstance(origin.get("origin"), str):
                continue
            if not isinstance(origin.get("localStorage"), list):
                continue
            # Only restore origin-scoped entries back to the origin where they
            # were captured. Never expose saved state to an unrelated website.
            safe_origins[origin["origin"]] = [
                {"name": item["name"], "value": item["value"]}
                for item in origin["localStorage"]
                if isinstance(item, dict) and isinstance(item.get("name"), str)
                and isinstance(item.get("value"), str)
            ]
        if safe_origins:
            script = """(() => {
                const saved = %s;
                const entries = saved[window.location.origin] || [];
                for (const item of entries) {
                    try { window.localStorage.setItem(item.name, item.value); } catch (_) {}
                }
            })();""" % json.dumps(safe_origins)
            context.add_init_script(script=script)
    except Exception as exc:
        print(f"[{profile}] Saved session could not be restored ({type(exc).__name__}); login required.")
        return False
    print(f"[{profile}] Restored saved browser session.")
    return True


def write_login_required_profiles(profiles: Iterable[str], path: Path | None = None) -> None:
    path = path or LOGIN_REQUIRED_PROFILES_PATH
    write_profile_list(profiles, path)


def read_login_required_profiles(path: Path | None = None) -> list[str]:
    path = path or LOGIN_REQUIRED_PROFILES_PATH
    return read_profile_list(path)


def write_profile_launch_failed_profiles(profiles: Iterable[str], path: Path | None = None) -> None:
    path = path or PROFILE_LAUNCH_FAILED_PROFILES_PATH
    write_profile_list(profiles, path)


def read_profile_launch_failed_profiles(path: Path | None = None) -> list[str]:
    path = path or PROFILE_LAUNCH_FAILED_PROFILES_PATH
    return read_profile_list(path)


def write_profile_list(profiles: Iterable[str], path: Path) -> None:
    profile_set = set(profiles)
    unique_profiles = [profile for profile in PROFILE_RUN_ORDER if profile in profile_set]
    path.parent.mkdir(parents=True, exist_ok=True)
    if unique_profiles:
        path.write_text("\n".join(unique_profiles) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def read_profile_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    profiles = []
    for line in path.read_text(encoding="utf-8").splitlines():
        profile = line.strip()
        if profile in PROFILES and profile not in profiles:
            profiles.append(profile)
    return profiles


def wait_for_page_ready(page, timeout_ms: int = 30000) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(3500)


def goto_product_rating(page) -> None:
    target_url = f"{PRODUCT_RATING_BASE_URL}?shop_region={SELLER_REGION}"
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            current_url = page.url or ""
            if current_url.startswith("about:"):
                if attempt == max_attempts:
                    raise
                print(
                    "Navigation failed while Chrome was still on the blank start page; "
                    f"retrying {attempt}/{max_attempts}. Error: {exc}"
                )
                page.wait_for_timeout(3000)
                continue
            if isinstance(exc, PlaywrightError) and "ERR_INTERNET_DISCONNECTED" in str(exc):
                if attempt == max_attempts:
                    raise
                print(
                    "Internet disconnected while opening TikTok Seller Center; "
                    f"retrying {attempt}/{max_attempts}. Error: {exc}"
                )
                page.wait_for_timeout(5000)
                continue
            print(
                "Navigation timed out after Chrome left the blank start page; "
                f"continuing with current URL: {current_url}. Error: {exc}"
            )
            return


def save_debug_screenshot(page, name: str) -> Path | None:
    try:
        path = Path("observations").joinpath(f"{name}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        print(f"Debug screenshot saved: {path}")
        return path
    except Exception as exc:
        print(f"Debug screenshot failed: {exc}")
        return None


def _dashboard_count(page, queue_name: str) -> int | None:
    """Re-open the dashboard and read the advertised queue count for reconciliation."""
    goto_product_rating(page)
    wait_for_page_ready(page)
    return read_dashboard_queue_state(page).count_for(queue_name)


def reset_review_scan_position(page) -> None:
    page.evaluate("""() => {
      for (const el of document.querySelectorAll('[data-codex-review-scan-started]')) {
        delete el.dataset.codexReviewScanStarted;
        el.scrollTop = 0;
      }
      const root = document.scrollingElement;
      if (root) root.scrollTop = 0;
    }""")


def raise_if_queue_login_required(profile: str, result) -> None:
    if result.outcome is QueueOutcome.LOGIN_REQUIRED:
        raise LoginRequiredError(result.reason or f"{profile} TikTok login is required")


def open_authoritative_queue(page, profile: str, queue: ReviewQueue) -> None:
    """Enter a queue only through its dashboard Reply now action and validate it."""
    for attempt in range(1, 4):
        open_review_queue(
            page,
            queue,
            target_url=f"{PRODUCT_RATING_BASE_URL}?shop_region={SELLER_REGION}",
            max_attempts=2,
            polls_per_attempt=15,
            poll_interval_ms=1000,
        )
        observed = wait_for_settled_scan(
            page,
            profile,
            queue.name,
            max_polls=20,
            poll_interval_ms=750,
            stable_observations=2,
        )
        if observed.state is PageState.LOADED_EMPTY:
            return
        if (
            observed.state is PageState.LOADED_WITH_ROWS
            and observed.cards
            and all(card.rating is not None and is_expected_queue_rating(card, queue) for card in observed.cards)
        ):
            return
        if attempt < 3:
            ratings = sorted({card.rating for card in observed.cards}, key=lambda value: (value is None, value or 0))
            print(
                f"[{profile}] {queue.name}: queue preflight mismatch "
                f"state={observed.state.value} rows={len(observed.cards)} ratings={ratings} "
                f"detail={observed.detail!r}; reloading and retrying {attempt}/3."
            )
            page.reload(wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
    raise NavigationError(f"{queue.name} queue did not contain only expected star ratings after 3 attempts")


def process_queue(page, profile: str, queue: ReviewQueue, args, log_path: Path) -> int:
    """Exhaust one queue, failing closed when detection or verification is uncertain."""
    since_date = parse_iso_date(args.since_date)
    print(f"[{profile}] Opening {queue.name} queue.")
    open_authoritative_queue(page, profile, queue)

    logged_states = load_logged_review_states(log_path)
    manually_logged: set[str] = set()
    decisions: dict[str, tuple[dict[str, str], ReplyDecision]] = {}

    def scan():
        return scan_review_page(page, profile, queue.name)

    def decide(card: ReviewCard) -> ItemDecision:
        if not is_expected_queue_rating(card, queue):
            print(
                f"[{profile}] {queue.name}: SAFETY SKIP rating={card.rating!r} "
                f"order={card.order_id or 'unknown'} is outside {','.join(queue.rating_filter)}"
            )
            return ItemDecision(ItemAction.HOLD_MANUAL)
        summary = extract_review_summary(card.text)
        legacy_key = make_review_key(profile, queue.name, summary)
        logged_state = logged_states.get(card.key) or logged_states.get(legacy_key)
        if logged_state is LoggedReviewState.SUBMITTED:
            return ItemDecision(ItemAction.SKIP_LOGGED)
        if logged_state is LoggedReviewState.AMBIGUOUS_MANUAL_CHECK:
            return ItemDecision(ItemAction.BLOCK_MANUAL)
        if logged_state is LoggedReviewState.HOLD_MANUAL:
            return ItemDecision(ItemAction.HOLD_MANUAL)
        review_date = summary.get("review_date", "") or card.review_date
        date_action = review_date_action(review_date, recent_days=args.recent_days, since_date=since_date)
        if date_action is ItemAction.HOLD_MANUAL:
            if card.key not in manually_logged:
                append_reply_log(log_path, ReplyLogEntry(
                    local_now(), profile, queue.name, ",".join(queue.rating_filter),
                    summary.get("order_id", ""), summary.get("username", ""),
                    summary.get("product", ""), summary.get("review_text", ""), "",
                    "needs_manual_review_missing_date", card.key,
                ))
                manually_logged.add(card.key)
            return ItemDecision(ItemAction.HOLD_MANUAL)
        if date_action is ItemAction.SKIP_OLD:
            return ItemDecision(date_action)
        reply_decision = select_reply_decision(queue, summary, args)
        decisions[card.key] = (summary, reply_decision)
        if not reply_decision.auto_submit:
            if card.key not in manually_logged:
                entry = ReplyLogEntry(
                    local_now(), profile, queue.name, ",".join(queue.rating_filter),
                    summary.get("order_id", ""), summary.get("username", ""),
                    summary.get("product", ""), summary.get("review_text", ""), "",
                    reply_decision.status, card.key,
                )
                append_reply_log(log_path, entry)
                manually_logged.add(card.key)
            return ItemDecision(ItemAction.HOLD_MANUAL)
        return ItemDecision(ItemAction.REPLY, reply_decision.reply_text)

    def submit(card: ReviewCard, reply_text: str):
        summary, reply_decision = decisions[card.key]
        print(
            f"[{profile}] {queue.name}: replying order={summary.get('order_id') or 'unknown'} "
            f"user={summary.get('username') or 'unknown'} category={reply_decision.category} "
            f"template={reply_decision.template_name}"
        )
        outcome = submit_review_reply(page, card, reply_text, submit=args.submit, rescan=scan)
        log_status = (
            "ambiguous_submit_manual_check"
            if outcome.outcome is SubmissionOutcome.AMBIGUOUS
            else outcome.outcome.value
        )
        entry = ReplyLogEntry(
            local_now(), profile, queue.name, ",".join(queue.rating_filter),
            summary.get("order_id", ""), summary.get("username", ""),
            summary.get("product", ""), summary.get("review_text", ""), reply_text,
            log_status, card.key,
        )
        append_reply_log(log_path, entry)
        if outcome.outcome is SubmissionOutcome.SUBMITTED:
            record_review_date(entry.review_key, summary.get("review_date", ""))
            manual_append_google_sheet_log(page, args, entry, summary.get("review_date", ""))
        # Keep the browser responsive and allow Playwright to observe page changes.
        page.wait_for_timeout(max(0, int(args.delay_after_reply * 1000)))
        if outcome.outcome in {SubmissionOutcome.SUBMITTED, SubmissionOutcome.DRY_RUN}:
            print(f"[{profile}] Re-entering {queue.name} queue after reply interaction.")
            open_authoritative_queue(page, profile, queue)
            reset_review_scan_position(page)
        return outcome

    def prepare_scan() -> None:
        # Walk virtualized lists one viewport at a time so rows between the top
        # and bottom are rendered and observed; jumping straight to the bottom
        # can permanently skip intermediate windows.
        page.evaluate("""() => {
          const candidates = Array.from(document.querySelectorAll('[class*="scroll" i], [class*="list" i], [role="list"]'))
            .filter(el => el.scrollHeight > el.clientHeight && el.clientHeight > 0)
            .sort((a, b) => b.clientHeight - a.clientHeight);
          const el = candidates[0] || document.scrollingElement;
          if (!el) return;
          if (el.dataset && !el.dataset.codexReviewScanStarted) {
            el.scrollTop = 0;
            el.dataset.codexReviewScanStarted = '1';
          } else {
            el.scrollTop = Math.min(el.scrollHeight - el.clientHeight,
                                    el.scrollTop + Math.max(1, Math.floor(el.clientHeight * 0.8)));
          }
        }""")
        page.wait_for_timeout(350)

    def wait_for_change() -> None:
        page.wait_for_timeout(500)

    def recover_on_empty() -> None:
        # A plain reload clears the queue selected by dashboard "Reply now"
        # and exposes all ratings again. Re-enter the authoritative queue so a
        # recovery scan remains restricted to Negative or Neutral reviews.
        # Page 2+ cannot be recovered this way without resetting pagination.
        if read_pagination(page).current_page > 1:
            return
        open_authoritative_queue(page, profile, queue)
        reset_review_scan_position(page)

    def advance_page() -> bool:
        pagination = read_pagination(page)
        if not pagination.has_next:
            return False
        if not go_to_next_page(page):
            raise RuntimeError("pagination reported another page but Next could not be clicked")
        page.wait_for_timeout(2500)
        changed = read_pagination(page)
        if changed.current_page == pagination.current_page:
            raise RuntimeError("Next was clicked but TikTok pagination did not advance")
        reset_review_scan_position(page)
        return True

    result = process_paginated_queue(
        scan,
        decide,
        submit,
        advance_page=advance_page,
        max_replies=args.max_replies_per_refresh,
        max_cycles_per_page=max(10, args.max_refresh_rounds * 10),
        prepare_scan=prepare_scan,
        wait_for_change=wait_for_change,
        recover_on_empty=recover_on_empty,
        dashboard_count=lambda: _dashboard_count(page, queue.name),
        dashboard_wait=lambda: page.wait_for_timeout(1500),
        dashboard_retries=3,
        queue_name=queue.name,
        min_stable_seconds=1.0,
    )
    raise_if_queue_login_required(profile, result)
    if result.outcome is not QueueOutcome.COMPLETE:
        diagnostic = save_debug_screenshot(page, f"{profile}_{queue.name}_{result.outcome.value}")
        if diagnostic:
            result = replace(result, diagnostic_paths=(str(diagnostic),))
    print(
        f"[{profile}] {queue.name}: outcome={result.outcome.value} submitted={result.submitted} "
        f"skipped_old={result.skipped_old} skipped_logged={result.skipped_logged} "
        f"held_manual={result.held_manual} dry_run={result.dry_run} "
        f"manual_check={result.manual_check} failed={result.failed} "
        f"remaining={result.remaining} dashboard_count={result.dashboard_count}"
    )
    if result.outcome is not QueueOutcome.COMPLETE:
        diagnostics = f" diagnostics={','.join(result.diagnostic_paths)}" if result.diagnostic_paths else ""
        raise RuntimeError(f"{profile} {queue.name} queue incomplete: {result.reason}{diagnostics}")
    return result.submitted


def browser_window_args(window_state: str) -> list[str]:
    if window_state == "minimized":
        return ["--start-minimized", "--window-size=1280,720"]
    if window_state == "maximized":
        return ["--start-maximized", "--window-size=1920,1080"]
    return ["--window-size=1280,720", "--window-position=80,80"]


def clear_orphan_profile_chrome(user_data_dir: str) -> None:
    # A killed or crashed run (or a stuck --mode login helper) can leave Chrome
    # processes alive that still hold this profile's lock. The next launch then
    # hands off to the orphan and immediately exits (exitCode 21), so the whole
    # profile "fails to launch" until someone kills Chrome by hand. Preflight:
    # kill only the Chrome processes whose command line targets THIS exact
    # user_data_dir (never the user's normal browsing Chrome), then drop the
    # stale singleton/lock files. Windows-only, matching the rest of this tool.
    if os.name != "nt":
        return
    pattern = f"*--user-data-dir={user_data_dir}*"
    # Kill matches, then wait (up to ~5s) for Chrome to release the process
    # singleton lock so the following launch does not race a half-dead orphan.
    ps = (
        "$pat = $env:UDD_PAT; "
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like $pat } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
        "for ($i = 0; $i -lt 25; $i++) { "
        "$n = @(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like $pat }).Count; "
        "if ($n -eq 0) { break }; Start-Sleep -Milliseconds 200 }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            env={**os.environ, "UDD_PAT": pattern},
            timeout=30,
            capture_output=True,
        )
    except Exception as exc:
        print(f"Orphan-Chrome preflight skipped for {user_data_dir}: {exc}")
    # Remove stale lock artifacts a crash may have left behind.
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        try:
            (Path(user_data_dir) / name).unlink()
        except (FileNotFoundError, OSError):
            pass


def launch_profile_context(chromium, config: dict[str, str], args, *, max_attempts: int = 2):
    last_error = None
    window_state = getattr(args, "browser_window_state", "minimized")
    for attempt in range(1, max_attempts + 1):
        try:
            # Self-heal from a prior orphaned/crashed session before launching so
            # a stuck run can never wedge every future one.
            clear_orphan_profile_chrome(config["user_data_dir"])
            # Real Chrome (channel="chrome") driven by Playwright never flushes
            # its cookie store to disk, so the persistent-context user_data_dir
            # loses the TikTok login on every close and each run lands back on
            # the Seller login page. We keep real Chrome for its lower bot-flag
            # profile and persist the login ourselves with a cookie jar (see
            # save_cookies / load_cookies_into) instead of trusting the on-disk
            # store.
            return chromium.launch_persistent_context(
                user_data_dir=config["user_data_dir"],
                channel=(os.getenv("BROWSER_CHANNEL", "chrome" if os.name == "nt" else "").strip() or None),
                headless=args.headless,
                no_viewport=True,
                args=browser_window_args(window_state),
            )
        except Exception as exc:
            last_error = exc
            print(f"Chrome launch attempt {attempt}/{max_attempts} failed for {config['label']}: {exc}")
            if attempt < max_attempts:
                time.sleep(3)
    raise last_error


def run_browser(args) -> list[str]:
    log_path = Path(args.log_path)
    failed_profiles = []
    login_required_profiles = []
    profile_launch_failed_profiles = []
    with sync_playwright() as p:
        for profile in profile_sequence(args.profile):
            config = PROFILES[profile]
            print("=" * 60)
            print(f"START PROFILE: {profile} ({config['label']})")
            print(f"MODE: {'SUBMIT REAL REPLIES' if args.submit else 'DRY RUN'} | REPLY ROUTING: {args.reply_mode}")
            print(f"GOOGLE SHEETS: {'ON' if args.google_sheets else 'OFF'}")
            print("=" * 60)
            try:
                context = launch_profile_context(p.chromium, config, args)
            except Exception as exc:
                failed_profiles.append(profile)
                profile_launch_failed_profiles.append(profile)
                print(f"[{profile}] PROFILE LAUNCH FAILED: {exc}")
                continue
            load_cookies_into(context, profile)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                for queue_name in args.queues:
                    process_queue(page, profile, REVIEW_QUEUES[queue_name], args, log_path)
                print(f"[{profile}] Finished all requested queues after refresh/recheck.")
                # Persist the refreshed/rotated session so tomorrow's run stays
                # logged in without re-injecting a stale jar.
                save_cookies(context, profile)
            except LoginRequiredError as exc:
                login_required_profiles.append(profile)
                failed_profiles.append(profile)
                print(f"[{profile}] LOGIN REQUIRED: {exc}")
            except Exception as exc:
                failed_profiles.append(profile)
                print(f"[{profile}] PROFILE INCOMPLETE: {exc}")
            finally:
                try:
                    context.close()
                except Exception as exc:
                    print(f"[{profile}] Browser context close warning: {exc}")
    report_path = Path(args.report_path).with_name(f"duoke_{datetime.now().astimezone().date().isoformat()}.md")
    write_duoke_report(log_path, report_path)
    print(f"Duoke report: {report_path}")
    if failed_profiles:
        print(f"Profiles failed: {', '.join(failed_profiles)}")
    if login_required_profiles:
        write_login_required_profiles(login_required_profiles)
        print(f"Login required for profiles: {', '.join(login_required_profiles)}")
    else:
        write_login_required_profiles([])
    if profile_launch_failed_profiles:
        write_profile_launch_failed_profiles(profile_launch_failed_profiles)
        print(f"Profile launch failed for profiles: {', '.join(profile_launch_failed_profiles)}")
    else:
        write_profile_launch_failed_profiles([])
    return failed_profiles


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID currently exists."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by a higher-privilege context (e.g. an elevated
        # scheduled-task run). Still counts as alive.
        return True
    except OSError:
        return False
    return True


def _read_login_helper_pidfile():
    """Return (pid, started_epoch) recorded by the running helper, or (None, None)."""
    try:
        raw = LOGIN_HELPER_PID_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    pid_part, _, started_part = raw.partition(",")
    try:
        pid = int(pid_part)
    except ValueError:
        return None, None
    try:
        started = float(started_part)
    except ValueError:
        started = None
    return pid, started


def _find_guard_port_owner(port: int):
    """Best-effort PID currently listening on 127.0.0.1:<port>, or None.

    Lets the guard name an untracked/legacy helper (one that never wrote a PID
    file) so the user still gets a concrete process to close instead of an
    unhelpful "no longer running" message. Windows-focused via netstat.
    """
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    needle = f":{port} "
    for line in out.splitlines():
        if "LISTENING" in line and needle in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1])
    return None


def _write_login_helper_pidfile() -> None:
    LOGIN_HELPER_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOGIN_HELPER_PID_PATH.write_text(f"{os.getpid()},{time.time()}", encoding="utf-8")


def _clear_login_helper_pidfile() -> None:
    try:
        LOGIN_HELPER_PID_PATH.unlink()
    except OSError:
        pass


def acquire_login_helper_guard():
    """Bind the single-instance guard port and return the listening socket.

    Returns None if a genuinely live, recent helper already owns it. Crashed or
    long-abandoned holders are reclaimed automatically so one bad run can no
    longer permanently block every future ``--mode login`` attempt.
    """
    for _attempt in range(2):
        guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            guard.bind(("127.0.0.1", LOGIN_HELPER_PORT))
            guard.listen(1)
        except OSError:
            guard.close()
        else:
            _write_login_helper_pidfile()
            return guard

        # Bind failed: something already holds the guard port. Explain what, and
        # decide whether it is safe to reclaim.
        pid, started = _read_login_helper_pidfile()
        if not pid or not _pid_alive(pid):
            # No usable marker. Ask the OS who actually owns the port so a
            # legacy/untracked helper can still be identified and cleared.
            owner = _find_guard_port_owner(LOGIN_HELPER_PORT)
            if owner and _pid_alive(owner):
                pid, started = owner, None
            else:
                print(
                    "A previous login helper is no longer running, but guard "
                    f"port {LOGIN_HELPER_PORT} was still busy. Clearing the "
                    "stale marker and retrying."
                )
                _clear_login_helper_pidfile()
                time.sleep(1.5)
                continue

        age = time.time() - started if started else None
        # Reclaim when the holder is clearly abandoned: too old, or an untracked
        # process squatting on our port with no known start time.
        if age is None or age > LOGIN_HELPER_STALE_SECONDS:
            age_note = (
                f"started ~{int(age // 60)} min ago"
                if age is not None else "start time unknown"
            )
            print(
                f"An existing login helper (PID {pid}, {age_note}) looks "
                "abandoned. Closing it and starting fresh."
            )
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(1.5)
            except OSError as exc:
                print(
                    f"Could not close PID {pid} automatically ({exc}). It is "
                    "probably running at a higher privilege level (e.g. a "
                    "scheduled task). Close it manually in an ADMIN terminal:\n"
                    f"  taskkill /PID {pid} /T /F"
                )
                return None
            _clear_login_helper_pidfile()
            continue

        # A recent, live helper genuinely owns the login window. Show the user
        # exactly why nothing new opened, and how to take it over.
        when = (
            datetime.fromtimestamp(started).strftime("%H:%M:%S")
            if started else "an unknown time"
        )
        age_txt = f"{int(age // 60)} min ago" if age is not None else "recently"
        print(
            "A TikTok login helper is already running, so no new window opened:\n"
            f"  PID {pid}, started {when} ({age_txt}).\n"
            "A Chrome login window should already be open for you — finish "
            "login there.\n"
            f"If you cannot find that window, close the helper and rerun:\n"
            f"  taskkill /PID {pid} /T /F"
        )
        return None

    print(
        f"Could not acquire the login guard port {LOGIN_HELPER_PORT} after "
        "retries. Wait a moment and try again."
    )
    return None


def run_login_helper(args) -> None:
    # A scheduled failure can recur before the user has completed login. Keep a
    # single helper alive so repeated Task Scheduler runs cannot create a pile of
    # PowerShell windows and blank Chrome tabs.
    #
    # The helper is manual: it opens Chrome and waits for a human to sign in and
    # press ENTER. If stdin is not an interactive terminal (a hidden or
    # scheduled run), that ENTER can never arrive and the process would hang
    # forever — holding both the guard port and a Chrome profile lock. That is
    # exactly what wedges later runs, so refuse to open a helper we cannot drive.
    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if not interactive:
        print(
            "Login mode needs an interactive console so you can sign in by hand, "
            "but stdin is not a terminal (this looks like a hidden or scheduled "
            "run). Skipping to avoid a stuck background helper. Run this yourself "
            "in a normal PowerShell window:\n"
            "  python auto_reply_reviews.py --mode login"
        )
        return

    helper_guard = acquire_login_helper_guard()
    if helper_guard is None:
        return

    try:
        profiles = (
            read_login_required_profiles()
            if args.login_required_only
            else profile_sequence(args.profile)
        )
        profiles = [profile for profile in profiles if profile in PROFILES]
        if not profiles:
            print("No TikTok profiles currently require login.")
            return

        with sync_playwright() as p:
            for profile in profiles:
                config = PROFILES[profile]
                print("=" * 60)
                print(f"LOGIN PROFILE: {profile} ({config['label']})")
                print("Chrome will open now. Login manually if TikTok asks.")
                print("After login is complete, come back here and press ENTER.")
                print("=" * 60)
                try:
                    context = launch_profile_context(
                        p.chromium,
                        config,
                        argparse.Namespace(headless=False, browser_window_state="maximized"),
                    )
                except Exception as exc:
                    print(f"[{profile}] LOGIN WINDOW SKIPPED: Chrome profile is already open or unavailable: {exc}")
                    continue
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(
                        f"{PRODUCT_RATING_BASE_URL}?shop_region={SELLER_REGION}",
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    input(f"Press ENTER after you finish login for {profile}... ")
                    time.sleep(2)
                    save_cookies(context, profile)
                finally:
                    context.close()
                print(f"Saved login session for {profile}.")
        print("All available selected profile login sessions are saved.")
    finally:
        helper_guard.close()
        _clear_login_helper_pidfile()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto-reply TikTok Shop negative and neutral product reviews.")
    parser.add_argument("--profile", choices=["all"] + list(PROFILES), default="all")
    parser.add_argument("--queues", nargs="+", choices=list(REVIEW_QUEUES), default=["negative", "neutral"])
    parser.set_defaults(submit=False)
    parser.add_argument("--submit", action="store_true", dest="submit", help="Explicitly opt in to submitting public replies (never the default).")
    parser.add_argument("--dry-run", action="store_false", dest="submit", help="Fill reply only and cancel before submitting.")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--browser-window-state",
        choices=["minimized", "normal", "maximized"],
        default="minimized",
        help="How Chrome opens during normal bot runs. Default: minimized so it does not steal your screen.",
    )
    parser.add_argument(
        "--reply-mode",
        choices=["manual_logic", "generic"],
        default="manual_logic",
        help=(
            "manual_logic (default) selects a context-specific reply from the "
            "review text. generic keeps the old one-message-per-star-queue behaviour."
        ),
    )
    parser.add_argument(
        "--negative-reply-text",
        default=DEFAULT_REPLY_TEXT,
        help="Fallback text for uncategorised negative reviews or --reply-mode generic.",
    )
    parser.add_argument(
        "--neutral-reply-text",
        default=DEFAULT_NEUTRAL_REPLY_TEXT,
        help="Fallback text for neutral reviews or --reply-mode generic.",
    )
    parser.set_defaults(google_sheets=os.getenv("GOOGLE_SHEETS_ENABLED", "false").lower() == "true")
    parser.add_argument(
        "--google-sheets",
        action="store_true",
        dest="google_sheets",
        help="Also manually append submitted reply KPI rows into the Google Sheet through Chrome.",
    )
    parser.add_argument(
        "--no-google-sheets",
        action="store_false",
        dest="google_sheets",
        help="Do not append submitted reply KPI rows into the Google Sheet.",
    )
    parser.add_argument(
        "--google-sheet-url",
        default=(
            "https://docs.google.com/spreadsheets/d/"
            f"{DEFAULT_GOOGLE_SHEET_ID}/edit?gid={DEFAULT_GOOGLE_WORKSHEET_GID}#gid={DEFAULT_GOOGLE_WORKSHEET_GID}"
        ),
        help="Google Sheet URL to open and manually paste KPI rows into.",
    )
    parser.add_argument(
        "--google-sheet-marker-path",
        default="logs/google_sheet_logged_keys.txt",
        help="Local marker file used to avoid manually pasting the same review twice.",
    )
    parser.add_argument(
        "--google-sheet-load-wait-ms",
        type=int,
        default=12000,
        help="How long to wait for Google Sheets to load before pasting.",
    )
    parser.add_argument(
        "--google-sheet-after-paste-wait-ms",
        type=int,
        default=2500,
        help="How long to wait after pasting before closing the Sheet tab.",
    )
    parser.add_argument(
        "--google-sheet-click-x",
        type=int,
        default=180,
        help="X coordinate to click inside the Google Sheet grid before keyboard navigation.",
    )
    parser.add_argument(
        "--google-sheet-click-y",
        type=int,
        default=220,
        help="Y coordinate to click inside the Google Sheet grid before keyboard navigation.",
    )
    parser.add_argument(
        "--google-log-attempts",
        action="store_true",
        help="Log failed/dry-run attempts to Google Sheets too. Default logs submitted replies only.",
    )
    parser.add_argument("--max-refresh-rounds", type=int, default=6)
    parser.add_argument("--max-replies-per-refresh", type=int, default=20)
    parser.add_argument("--delay-after-reply", type=float, default=4.0)
    parser.add_argument(
        "--recent-days",
        type=int,
        default=30,
        help="Only reply to reviews from the last N days. Use 0 to allow all dates.",
    )
    parser.add_argument(
        "--since-date",
        default="",
        help="Only reply to reviews on/after YYYY-MM-DD. Overrides --recent-days when set.",
    )
    parser.add_argument("--log-path", default="logs/replied_reviews.csv")
    parser.add_argument("--report-path", default="reports/duoke_today.md")
    parser.add_argument("--alert-path", default="alerts/latest_alert.txt")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--mode", choices=["run", "report", "alert", "login"], default="run")
    parser.add_argument(
        "--login-required-only",
        action="store_true",
        help="In login mode, open only profiles recorded in alerts/login_required_profiles.txt.",
    )
    parser.add_argument("--report-date", default="", help="YYYY-MM-DD date for report mode. Defaults to today.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.mode == "report":
        date_prefix = args.report_date or datetime.now().astimezone().date().isoformat()
        report_path = Path(args.report_path).with_name(f"duoke_{date_prefix}.md")
        write_duoke_report(Path(args.log_path), report_path, date_prefix=date_prefix)
        print(f"Duoke report: {report_path}")
        return
    if args.mode == "alert":
        date_prefix = args.report_date or datetime.now().astimezone().date().isoformat()
        report_path = Path(args.report_path).with_name(f"duoke_{date_prefix}.md")
        message = write_alert_summary(
            Path(args.log_path),
            Path(args.alert_path),
            date_prefix=date_prefix,
            exit_code=args.exit_code,
            report_path=report_path,
        )
        print(message)
        return
    if args.mode == "login":
        run_login_helper(args)
        return
    failed_profiles = run_browser(args)
    if failed_profiles:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
