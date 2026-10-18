# Setup and daily operation

TikTok review checks and BigSeller stock exports run on GitHub Actions. Telegram
controls and the temporary login viewer run in the `tiktok-review-control`
Cloudflare Worker. Your computer does not need to stay on for hosted runs.

For an existing deployment, use the commands in [README.md](README.md) and the
release steps in [DEPLOY_NOTES.md](DEPLOY_NOTES.md). The setup wizard below is for
bootstrapping a private repository from the original Windows backup. It sets
public replies off and is not the routine way to update a running deployment.

## Bootstrap a new private deployment

1. Install Python 3.12 or newer, Git, and GitHub CLI. Authorize your GitHub account
   when the wizard opens the browser.
2. Create a Telegram bot with BotFather. The wizard prompts for its token without
   displaying it, then asks you to send `/start` privately to identify the alert
   recipient. This initializes outbound alerts; the interactive Worker controls
   require the separate Cloudflare configuration in the deployment notes.
3. Put the original `Tiktok Auto Reply Negative Reviews.zip` in this directory or
   your Downloads directory. On Windows, run `FINISH_SETUP_WINDOWS.cmd` or
   `FINISH_SETUP_WINDOWS.ps1`. On other systems, install `requirements-github.txt`
   and run `python -m scripts.finish_setup --original-zip /path/to/backup.zip`.
4. The wizard creates a private repository, stages the source allowlist, stores
   secrets in GitHub Actions, and uploads encrypted history and browser sessions
   to the separate `automation-state` branch. It preserves existing cloud state.
5. Open the resulting Actions page. Review `check-setup.yml` and the initial
   check with public replies disabled. Complete interactive login for any expired
   sessions, then run a dry run for each configured shop.
6. After checking review eligibility, duplicate protection, and the Sheet writer,
   set `ENABLE_SUBMIT=true` to permit public replies. Disable old Windows tasks
   that would run the same reviews or produce duplicate stock exports.

`AUTOMATION_SHOPS` selects a comma-separated list from `welive_main`,
`snackersbox`, and `konomi_living`. The default is all three. Set it in the local
setup environment before bootstrapping to select fewer shops; the wizard stores
that selection as the GitHub Actions variable. Only selected shops need imported
sessions. Review checks run sequentially at 09:15 Asia/Singapore when
`AUTOMATION_ENABLED=true`. GitHub can delay scheduled starts.

## Connect the existing KPI Sheet

The configured Sheet is **TikTok Negative Review KPI**, tab **Backend Data**
(`gid=298261214`). Keep its existing rows and columns.

For browser mode, set `GOOGLE_SHEETS_MODE=browser`. The saved browser session for
each shop must include Google access to the Sheet as well as TikTok access. The
writer reads CSV through that authenticated browser and appends only verified
new replies. Dry runs disable Sheet writes. Do not make the Sheet public to
work around missing Google login.

For local session preparation, install Chromium with `python -m playwright install
chromium`, then run:

```sh
python auto_reply_reviews.py --mode login --profile welive_main
```

Complete TikTok and Google sign-in in that browser before confirming in the
terminal. Repeat for other selected profiles. This saves local sessions; it does
not upload them or replace the cloud ledger. Use the private Telegram login flow
for routine hosted TikTok session refreshes.

API mode is optional. Share the same Sheet with a Google service account as an
Editor and provide its JSON file using the wizard's `--google-credentials` option.
The wizard validates headers without writing before uploading the credential as
a GitHub secret. Never commit that JSON file. Without configured browser or API
access, keep `GOOGLE_SHEETS_MODE=off`.

## Refresh expired logins

Send `/tt_login` privately and choose a shop, or use `/tt_login welive_main`.
Open the private link, complete login and OTP, open Product Ratings for the
correct shop, and tap **Save login**. Telegram confirms the verified save.
Existing history is restored before saving the refreshed session.

For BigSeller, tap **Login to BigSeller** in an expiry alert or send `/bs_login`.
Open Inventory List after signing in, then tap **Save login**. BigSeller sessions
are encrypted independently on the `bigseller-state` branch.

On a phone, tap the remote screen to open the keyboard. **Zoom** enlarges the
screen; **Pan** lets you drag it around. Turn Pan off to type, or use **Next field**
and **Enter** above the keyboard. The keyboard button shows or hides typing.
Sessions close after 15 minutes of browser availability. Failed login attempts
preserve the last saved state.

Expiry is detected during scheduled or manual checks. Ambiguous public replies
require inspection in Seller Center before any retry; refreshing login does not
clear the duplicate ledger.

For stock-sync setup, schedules, importer settings and failure recovery, see
[BigSeller automation](docs/bigseller-automation.md).
