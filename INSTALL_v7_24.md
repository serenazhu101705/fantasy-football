# Fantasy Coach v7.24 — tab logo and ESPN player headshots

Incremental frontend-only patch for the v7.22/v7.23 frontend. **No changes** to your email delivery configuration, GitHub Actions, ESPN authentication, lineup writes, or backend data code.

## Install

From the root of your `fantasy_football` Git repo:

```bash
# Check local changes before overwriting App.tsx and styles.css
 git status --short

unzip -o ~/Downloads/fantasy_coach_v7_24_favicon_player_photos.zip

uv run python -m unittest discover -s tests -p 'test_v724_player_presentation.py' -v

cd web
npm run build
npm run dev
```

If you already have `npm run dev` running, Vite usually picks up the changes automatically. If not, restart it and refresh the page.

### Browser tab logo

The new `web/public/favicon.svg` matches the existing dark-trophy-on-lime-green brand mark in the sidebar. `web/index.html` links to it using a cache-busting `?v=724` URL. If Chrome still shows the globe, visit `http://localhost:5173/favicon.svg?v=724` to confirm Vite serves it and use `Command + Shift + R` to hard refresh the main page. If your Vite port differs, use that port.

### Player photos

Player profiles and roster/waiver rows use the **same numeric ESPN player ID that already opens the profile**:

`https://a.espncdn.com/i/headshots/nfl/players/full/3918298.png`

The ESPN CDN is publicly accessible and **does not consume Anthropic credits**. Missing images, team-defense entries, and players without valid ESPN athlete IDs display a branded initials avatar. We do not guess a photo using the player's name. Images are loaded directly from ESPN with native lazy loading for list thumbnails.

## Files

- `web/index.html` — favicon link.
- `web/public/favicon.svg` — favicon matching existing app branding.
- `web/src/PlayerHeadshot.tsx` — reusable ESPN thumbnail/photo component.
- `web/src/App.tsx` — adds thumbnails to player rows and large portraits to profile drawers.
- `web/src/styles.css` — dark-theme photo styling and small-screen adjustments.
- `tests/test_v724_player_presentation.py` — offline contract checks.

## Test and commit

After verifying the browser, use `git diff` and then commit only these files:

```bash
git add web/index.html web/public/favicon.svg web/src/PlayerHeadshot.tsx web/src/App.tsx web/src/styles.css tests/test_v724_player_presentation.py
git commit -m "Add branded favicon and ESPN player portraits"
git push
```

**Note:** This patch uses the latest frontend source available from the v7.22 patch, which v7.23 did not modify. If you made new local edits to `web/src/App.tsx` since installing v7.22, review `git diff` before accepting the replacement.

## Validation scope

The Python contract tests and TypeScript *syntax/transpilation* checks can run offline. A full production Vite build needs npm dependencies installed, and actual headshot downloads must be checked in your running browser; the offline checks do not establish those are successful.
