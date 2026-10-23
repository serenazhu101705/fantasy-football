# Fantasy Coach — free off-Mac email automation (GitHub Actions + Resend)

**What this does:** Run the existing Fantasy Coach Python digest in a private GitHub Actions runner five times per day, whether your Mac is on or off. Delivers through Resend HTTPS, no Railway or hosted web app required. Supports Thursday lineup, bye alerts, injury news, and the post-Monday recap when ESPN says games are final. The game recap still uses paid Claude API tokens when first created.

**What this does not do:** Host the React website, change your actual ESPN lineup, deliver at an exact guaranteed minute, or accept new external subscriber signups. This is for **one inbox only**, configured in GitHub Secrets. The already-confirmed *local* SQLite subscription does not automatically copy to GitHub; the runner provisions your secret-configured email as its own personal subscription. You will not have to confirm a second subscription link.

## 1. Install and test locally

In the root of your **private** `fantasy_football` Git repository:

```bash
unzip -o ~/Downloads/fantasy_coach_github_email_free.zip
uv run python -m unittest discover -s tests -p 'test_github_personal_email.py' -v
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

The ZIP changes `app/email_subscriptions.py` to support Resend HTTPS (local SMTP still works), and adds the GitHub workflow, owner-only subscriber bootstrap, protected saved-state restoration, and tests. It doesn't touch ESPN write or lineup logic.

Check that `.env`, `data/*.sqlite3`, cookies, tokens and local caches are NOT being committed. Your GitHub repository must be **private**.

```bash
git status
git add .github/workflows/fantasy-coach-emails.yml \
  .github/scripts/restore_email_state.py \
  app/email_subscriptions.py app/github_email_bootstrap.py \
  tests/test_github_personal_email.py INSTALL_GITHUB_EMAIL.md
git commit -m 'Add free private GitHub Actions email scheduler'
git push
```

If your default branch is protected, push a feature branch and merge a PR first. Scheduled Actions run from the repository's **default branch**.

## 2. Add GitHub Secrets

On GitHub: **Repository → Settings → Secrets and variables → Actions → New repository secret**. Add each of these *names* with values from your local `.env` or Resend dashboard:

| Secret | Value |
| --- | --- |
| `FANTASY_OWNER_EMAIL` | The single destination address that already received your Resend SMTP test |
| `ESPN_LEAGUE_ID` | Your ESPN fantasy league ID |
| `ESPN_TEAM_ID` | Your ESPN team ID |
| `ESPN_S2` | Your private ESPN cookie |
| `ESPN_SWID` | Your private ESPN cookie |
| `ANTHROPIC_API_KEY` | Your existing Anthropic API key; recap may use paid tokens |
| `RESEND_API_KEY` | Your existing Resend SMTP API key, reused for Resend HTTPS |

Never paste secret **values** in Issues, pull requests, chat, logs or tracked YAML. Do not give access to others to run arbitrary workflows with these credentials.

The workflow sends from `Fantasy Coach <onboarding@resend.dev>`, suitable only for the recipient authorized by your Resend account. If you have verified your own domain, replace `RESEND_FROM_EMAIL` in the workflow with a sender from that verified domain.

## 3. Run once from GitHub

1. Open your private repo → **Actions** → **Fantasy Coach Emails**.
2. Choose **Run workflow** on the **default branch**.
3. Open the run logs. Look for `Email state: new` (first run), then `Personal Fantasy Coach email state: initialized`, and a `Digest run: ... errors=[]` line.
4. After success, expand the workflow's **Artifacts**. It will save `fantasy-coach-email-state`. The next run will restore it and report `Email state: restored`.
5. Emails are sent only if due under your app's existing rules. On the first run, the cloud state starts fresh and may resend an event you already got from the local Mac scheduler. Subsequent runs should deduplicate by event and week.

**If the action fails:** Review the red step in the GitHub Actions log. Common causes: missing GitHub Secret, wrong ESPN cookie, provider throttling, or dependency installation. Don't enable ESPN writes to solve an email failure.

## 4. Schedule and stop the local duplicate sender

GitHub runs the workflow around **6:17 AM, 9:17 AM, 1:17 PM, 5:17 PM and 9:17 PM America/New_York**, including daylight saving adjustments. The digest script itself decides which messages are due. GitHub scheduled runs can be delayed or occasionally skipped; exact times aren't guaranteed. Post-MNF recaps should arrive once ESPN finalizes every game, normally Tuesday morning (or later if ESPN delays final scores).

Once the GitHub emails work, stop the old Mac LaunchAgent so you don't receive duplicates from two independent SQLite ledgers:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.fantasycoach.digest.plist
mv ~/Library/LaunchAgents/com.fantasycoach.digest.plist \
  ~/Library/LaunchAgents/com.fantasycoach.digest.plist.disabled
```

If `bootout` says it's not loaded, you can still rename the plist file to prevent relaunching at login.

To stop receiving cloud emails, go to the GitHub **Actions** tab, select **Fantasy Coach Emails**, then use the `...` menu to disable the workflow. The emails deliberately link to this control rather than to a broken `localhost` unsubscribe link.

## 5. Persistence, free-tier limits, and safety

- GitHub-hosted jobs are **temporary virtual machines**. The workflow saves the subscriber/delivery-history SQLite DB plus cached generated recaps as a private Actions artifact; it restores the newest saved state before sending. It uses GitHub Actions caching separately for source data to avoid needless FantasyPros calls. The email state artifact has a 30-day retention setting. If all artifacts are removed/expire or the repository is reset, the state might be lost and an earlier event could be emailed again. A multiuser production subscription system would need a durable database instead.
- The workflow refuses to run its personal subscriber bootstrap if the GitHub repo isn't **private** or ESPN auto-writes are enabled; it also refuses to email other confirmed recipients from a restored DB.
- Resend Free currently allows up to **3,000 emails/month and 100/day**. The Resend API is used over HTTPS so port-blocking on third-party hosts isn't a problem.
- GitHub Free currently includes **2,000 Actions minutes/month for private repositories**. Usage is not unlimited. Watch repository Actions usage and set a spending cap you are comfortable with to avoid overage. If the backend takes a few minutes per run, five runs/day should usually be within this allowance, but measure the actual job duration.
- Because this is *your private fantasy league*, **never change the repository visibility to public** without first removing stored artifacts and credentials. Keep ESPN write variables explicitly set to `0`.
- Successful email delivery still depends on ESPN/Resend/Anthropic availability and API access; this was tested offline, not executed with your live GitHub account or credentials.

Official docs: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows ; https://docs.github.com/en/actions/reference/limits ; https://resend.com/pricing
