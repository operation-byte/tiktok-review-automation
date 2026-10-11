"""Best-effort Telegram signal when GitHub fails before the normal daily report."""
import os
import urllib.parse
import urllib.request


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
    allowed = set(os.getenv("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(","))
    if not token or not chat or chat not in allowed or not chat.isdecimal():
        print("Telegram alert skipped: not configured")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    if os.getenv("FAILURE_CONTEXT", "").strip() == "relogin":
        text = "⚠️ TikTok relogin browser stopped unexpectedly (failed or timed out). Your saved login was not changed. Use /tt_login to request a fresh link."
    else:
        text = "⚠️ TikTok daily GitHub Actions check FAILED or needs attention. Open your private GitHub Actions run for details. No automatic rerun will post replies until the next check."
    payload = urllib.parse.urlencode({
        "chat_id": chat,
        "text": text,
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=payload, method="POST"), timeout=12):
            pass
    except Exception:
        print("Telegram send failed (no sensitive details logged)")


if __name__ == "__main__":
    main()
