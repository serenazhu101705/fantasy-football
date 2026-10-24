# Fantasy Coach — deploy the website + API on Render Free (Mac can be off)

This is an **incremental patch** for your existing private `fantasy_football` repository, including the latest v7.27 waiver explanations and v8.0 mobile-aware frontend. It creates **one password-protected HTTPS site** containing the React web app and FastAPI API. The separate GitHub Actions email workflow remains responsible for automatic emails.

**What free means:** Render Free spins down after 15 minutes of no traffic and starts on the next request (often ~1 minute). It offers limited CPU/RAM and monthly limits. It is appropriate for trying your own app, **not a guaranteed always-on service**. File changes (including SQLite databases) disappear on restart, redeploy, and spin-down. The app's email subscription UI **will not be durable** on this setup. Keep using your existing GitHub Actions workflow for emails. Anthropic API is billed separately. Do not enable ESPN lineup writes remotely.

## 1. Install and test on your Mac

From the **repository root** (`fantasy_football`, not `web`):

```bash
unzip -o ~/Downloads/fantasy_coach_render_free_hosting.zip
uv run python -m unittest discover -s tests -p 'test_render_free_deploy.py' -v
cd web && npm install && npm run build && cd ..
git status --short
```

Review `git diff` before pushing. The patch adds Render-specific `Dockerfile`, `.dockerignore`, `cloud_start.py`, `app/deploy_api.py`, `app/cloud_security.py`, tests, and PWA icons/manifest. `web/index.html` adds the PWA metadata; preserve custom HTML changes if yours differs. It does **not** overwrite `app/api.py`, `app/mobile_access.py`, `web/src/App.tsx`, your GitHub email workflow, or any `.env` file.

Check `.gitignore` before pushing. NEVER commit `.env`, API keys, ESPN cookies, or local SQLite files. Commit and push the patch using Git as usual:

```bash
git add Dockerfile .dockerignore cloud_start.py app/cloud_security.py app/deploy_api.py tests/test_render_free_deploy.py web/index.html web/public/apple-touch-icon.png web/public/icon-192.png web/public/icon-512.png web/public/manifest.webmanifest INSTALL_RENDER_FREE.md
git commit -m "Host private Fantasy Coach dashboard on Render Free"
git push
```

Ensure **all earlier code** is already pushed, including `pyproject.toml`, `uv.lock`, `web/package.json` and `web/package-lock.json`. If a lockfile was changed by `npm install`, review and commit it too.

## 2. Create Render free web service

1. Visit https://dashboard.render.com, sign up, and connect GitHub.
2. Choose **New → Web Service** (not Static Site, because FastAPI is needed).
3. Connect your **private** Fantasy Coach GitHub repo.
4. Choose **Runtime: Docker**, Dockerfile at repo root `./Dockerfile`, branch `main` (or your actual branch), and **Instance Type: Free**. No disk or volume is available on Free.
5. If Render requests a start command, use the Dockerfile default (`python cloud_start.py`); do **not** run a second standalone Uvicorn process.
6. In **Environment**, set server-only secrets (no `VITE_` prefix):

```dotenv
FANTASY_WEB_USERNAME=choose-a-login-name
FANTASY_WEB_PASSWORD=generate-a-long-random-password
FANTASY_MOBILE_ACCESS_KEY=generate-a-different-random-32-plus-character-key
ESPN_LEAGUE_ID=your-league-id
ESPN_TEAM_ID=your-team-id
ESPN_S2=your-private-espn-s2-cookie
ESPN_SWID=your-private-espn-swid-cookie
ANTHROPIC_API_KEY=your-private-claude-api-key
ANTHROPIC_MODEL=your-current-working-model-identifier
ESPN_WRITES_ENABLED=0
ESPN_AUTO_SWAPS_ENABLED=0
FANTASY_EMAIL_SCHEDULER_ENABLED=0
FANTASY_DEBUG=0
```

Generate independent random values on your Mac with:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(36))'
```

Run twice, once for each secret. Store them **only** in Render's Environment settings, not in GitHub source or client-side `VITE_` variables. The mobile-access key is needed because v8.0 added an API guard for remote callers; authenticated browser requests are bridged server-side to that guard. You do **not** enter this key in Safari; Safari prompts for `FANTASY_WEB_USERNAME` and `FANTASY_WEB_PASSWORD`.

**Environment variable naming:** Match the exact variable names your working `.env` and installed backend expect. Do not blindly copy placeholders. If `ANTHROPIC_MODEL` is not set locally, leave it out and let your code use its default.

7. Deploy. Render builds React and the Python environment, then starts FastAPI.
8. Open the HTTPS URL Render assigns, e.g. `https://<your-service>.onrender.com`. Log in with the site username/password. Test Overview, My Roster, Waiver Wire and Chat.
9. Test the status endpoint: `https://<your-service>.onrender.com/api/health`. It does not require login. Optionally set Render's HTTP health check path to `/api/health`.

If the service fails, open Render **Logs**. Common causes: missing repository files, an outdated `web/package-lock.json`, unresolved Python dependencies, insufficient free RAM/CPU, or ESPN credentials missing. The local build does not prove a Render build works; this patch was **not live deployed**.

## 3. Install on your physical iPhone

On your iPhone, open the **Render HTTPS URL** in Safari (not the old `192.168.x.x` or localhost URL). Sign in. Tap **Share → Add to Home Screen**, enable **Open as Web App** if prompted, and tap **Add**. The existing green trophy icon is included as an Apple touch icon.

The installed Home Screen app opens the hosted web app directly. You **do not** need Xcode, CocoaPods, a macOS upgrade, an iOS developer account, or an App Store release. The Mac can be off. Internet access is needed for live features, and Render may take a minute to wake after idle.

## 4. What changes and what does not

- **Website + API:** Render, independent of Mac. Both React and FastAPI share one origin; no CORS proxy or separate static host needed.
- **Emails:** The GitHub Actions/Resend scheduler you've already set up, independent of this Render service. Leave `FANTASY_EMAIL_SCHEDULER_ENABLED=0`.
- **Database:** Render Free cannot keep a local SQLite database across service restarts/sleep. Browser sessions, locally cached Claude analyses and subscriptions may be lost. GitHub email-delivery state is separate and unaffected. Do **not** rely on the website's Email & Automation signup to update the GitHub Actions subscription.
- **Native Capacitor app:** This patch doesn't compile or distribute an `.ipa`. If you later install native iOS with Xcode, it needs its own authentication design; for now use the Home Screen web app.
- **Money:** Render free hosting within limits, and no new software fees. Anthropic API requests can still incur cost. Configure workspace spending controls or omit a payment method if supported, and inspect the latest pricing/quotas.
- **Security:** Always use HTTPS. Basic authentication is a lightweight safeguard appropriate for a personal prototype, not a full multi-user account system. Never commit or paste secrets. Do not expose development Uvicorn or Vite servers to the internet.

## References

- Render Free: https://render.com/docs/free
- Render deploy: https://render.com/docs/your-first-deploy
- Apple Home Screen web app: https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/27/ios/27
