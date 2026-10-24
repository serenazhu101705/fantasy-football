# Fantasy Coach — Auto Swap switch on the Optimal Lineup page

This incremental patch puts an owner-only toggle on **Optimal Lineup**, backed by **one durable GitHub Actions repository variable**. It works on your Render-hosted iPhone Home Screen app with your Mac off.

**Why GitHub?** Render Free sleeps and loses its writable disk. Your existing GitHub Actions workflow (not Render) schedules fantasy digest jobs. A local switch or an environment variable on Render would not reliably control that worker.

## 1. Install in your current repo

Download and unzip this archive into your `fantasy_football` repository root:

```bash
cd ~/fantasy_football
unzip -o ~/Downloads/fantasy_coach_autoswap_switch.zip
python3 install_autoswap_switch.py
uv run python -m unittest discover -s tests -p 'test_autoswap_switch.py' -v
cd web && npm run build && cd ..
git diff --stat
git diff
```

**Important:** The installer edits your **current** `app/api.py`, `app/run_digest_jobs.py`, `app/github_email_bootstrap.py`, `.github/workflows/fantasy-coach-emails.yml`, `web/src/App.tsx` and `web/src/styles.css` in place, preserving other recent changes (such as iPhone adjustments). It refuses to edit files when its insertion anchors do not match. It is safe to rerun. New file `app/auto_swap_control.py` contains the GitHub setting integration. No credentials are included.

After reviewing `git diff`, commit and push:

```bash
git add app/auto_swap_control.py app/api.py app/run_digest_jobs.py app/github_email_bootstrap.py \
  .github/workflows/fantasy-coach-emails.yml web/src/App.tsx web/src/styles.css \
  tests/test_autoswap_switch.py install_autoswap_switch.py INSTALL_AUTOSWAP_SWITCH.md
git commit -m "Add persistent Auto Swap toggle to lineup optimizer"
git push
```

If you haven't set up `.github/workflows/fantasy-coach-emails.yml` yet, add the **original** GitHub email integration first. This patch modifies that existing workflow.

## 2. Set up the shared on/off flag in GitHub (safe default: OFF)

1. Open your **private** GitHub fantasy-football repository.
2. Open **Settings → Secrets and variables → Actions → Variables**.
3. Create a **repository variable** named `FANTASY_AUTO_SWAP_ENABLED` with value **`0`**.
4. Make sure the existing GitHub scheduled **Fantasy Coach Emails** workflow works when this variable is `0` before enabling anything. Existing `FANTASY_OWNER_EMAIL`, `ESPN_LEAGUE_ID`, `ESPN_TEAM_ID`, `ESPN_S2`, `ESPN_SWID` GitHub *secrets* must still be configured.

This boolean is not a secret. It is deliberately distinct from the ESPN credentials and the website login. It persists across Render redeploys.

## 3. Give Render narrowly scoped permission to update this one repository's variables

1. In your GitHub account: **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token** (or https://github.com/settings/personal-access-tokens).
2. Choose the owner of the private fantasy repo, select **Only select repositories**, and choose only the Fantasy Coach repository.
3. Under **Repository permissions**, grant **Variables: Read and write**. Leave other repository permissions at their minimum/default. Set a sensible expiration date and plan to rotate it.
4. Generate the token and copy it once. **Do not paste the token into ChatGPT, GitHub source, a `VITE_` variable or your iPhone.**
5. In **Render → fantasy-football → Environment**, add:

```dotenv
FANTASY_AUTOSWAP_GITHUB_REPO=YOUR_GITHUB_USERNAME/YOUR_PRIVATE_FANTASY_REPO
FANTASY_AUTOSWAP_GITHUB_TOKEN=YOUR_FINE_GRAINED_PAT
```

Use the exact `owner/repository` shown in GitHub's URL. Store the token in Render **server-side environment settings only**. Click **Save, deploy**.

You do **not** need to give Render your general GitHub account password. This token does not need access to repository contents, secrets, or workflows. GitHub's fine-grained permission for this REST API is `Variables (write)` on the selected repository.

## 4. How the toggle works

After the newest commit deploys on Render:

1. Open Fantasy Coach → **Optimal Lineup**.
2. Find **Auto Swap**. Its state loads from GitHub; if GitHub cannot be reached, the setting is **not** assumed ON.
3. Flip **ON** and confirm the warning. A server-side request writes `FANTASY_AUTO_SWAP_ENABLED=1` to the GitHub repository variable and verifies it by reading it back.
4. At the next GitHub scheduled run, your private worker enables the *existing* guarded injury/bye swap path, but only for the configured owner and only when lineup/kickoff safety checks pass. It rechecks the GitHub variable before ESPN submission.
5. Flip **OFF** to write `0`. Future worker checks refuse automatic ESPN writes. An already-completed ESPN change cannot be undone by the switch. A transaction already in progress at the moment of disabling may still finish.

**This is NOT a real-time injury monitor.** Your existing workflow checks at approximately 6 AM, 9 AM, 1 PM, 5 PM, and 9 PM Eastern. GitHub may delay jobs. For example, a Sunday 12:40 PM scratch may be missed by a check at 9 AM, and the 1 PM check may be too late. We can schedule more frequent pre-kickoff checks separately if desired, with associated GitHub Actions usage.

This uses the existing `automatic_injury_bye_lineup`, `create_preview` and `submit_preview` code. It is intended to swap OUT/IR/suspended/bye players only, **not** maximize projections for healthy starters, draft players, or submit waivers. ESPN's roster-write API is undocumented; changes are best-effort and may be rejected. Verify your roster on ESPN, especially before games.

**Manual lineup changes are separate.** Your current `cloud_start.py` forcibly sets `ESPN_WRITES_ENABLED=0` on Render. Changing that setting on Render alone does **not** enable manual lineup writes on the website. This patch leaves Render's write guard intact, while it allows *only your GitHub Actions worker* to opt into the separately verified Auto Swap path.

## 5. Troubleshooting

- **Switch says cannot connect**: verify both Render variables, correct private repo slug and GitHub PAT permission `Variables: Read and write`. Check that the PAT is unexpired and that Render has redeployed.
- **Switch turns on but no swaps occur**: check your *GitHub Actions* workflow run logs and confirm the private repo, owner email, credentials and ESPN game's kickoff/lock information. The worker only acts when scheduled and only for eligible verified replacements.
- **Switch doesn't persist after a Render restart**: inspect GitHub **Actions → Variables**; the `FANTASY_AUTO_SWAP_ENABLED` repository variable, not local SQLite, is authoritative.
- **GitHub refuses to enable variables**: test permission with a new scoped PAT; avoid giving a broad repository administrator token.
- **Need to immediately disable remotely**: manually change the GitHub repository variable `FANTASY_AUTO_SWAP_ENABLED` to `0`, regardless of whether Render is awake.

Tested with offline mocked GitHub API (8 unit tests), Python compilation, patch idempotence and TSX syntax transpilation. **Not tested against your live Render/GitHub account or the ESPN write endpoint.**
