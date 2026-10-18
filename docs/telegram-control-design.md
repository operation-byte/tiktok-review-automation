# Telegram review controls

Use a Cloudflare Worker with a Durable Object to authenticate and deduplicate
`/tt_*` and `/bs_*` commands and their login buttons from the owner's private chat. Forward all other updates to the
existing stock-count Worker. Keep its deployment intact.

Commands: help, status, errors, run, dryrun, pause, resume, login (one TikTok shop
or the BigSeller account). TikTok also offers loginstatus.
GitHub workflows remain the only review runner, with the shared concurrency
lock and encrypted state. Pause changes AUTOMATION_ENABLED; new runs check it.
No command automatically clears ambiguous submissions or reply history.

Relogin uses a bounded GitHub job with Xvfb, Chromium and loopback-only VNC.
An outbound authenticated WebSocket carries VNC through the Worker to a
mobile-compatible noVNC page. No runner port or public tunnel is exposed.
The private Telegram link exchanges a random fragment token for a Secure,
HttpOnly cookie. Links/sessions expire; sockets and browser close at job end.
Credentials are typed directly into the remote TikTok or BigSeller browser,
never into chat. A phone screen tap opens the keyboard within the original
touch gesture; Zoom/Pan and keyboard navigation remain local viewer controls.
The relay does not log frames, input or screenshots. Only verified sessions
are saved. TikTok restores its latest cloud ledger under the review job lock;
BigSeller has a separate encrypted state branch and browser job lock.

Test authorization, command parsing, duplicate updates, pause/live gates,
WebSocket access and expiry; verify Worker deployment, Telegram delivery,
GitHub dry run and browser transport. Human authentication may remain necessary
if either service requires an OTP or rejects a hosted browser.
