# Fantasy Coach v8.0 — iPhone app (Capacitor)

This is an incremental patch on top of Fantasy Coach v7.27. It turns the existing React/Vite UI into an **iOS app built and signed with Xcode**, without duplicating the web app. It also adds a private API connection screen, iPhone bottom navigation/safe-area styling, your existing trophy app icon, and an opt-in remote-access guard for FastAPI. Existing GitHub Actions email automation remains unchanged.

**Important:** This patch is iOS-ready **source code, not a signed `.ipa` or App Store app**. The app frontend runs on the iPhone, but ESPN, Claude, and all other live data features still require the Python FastAPI backend running somewhere reachable. GitHub Actions is an email scheduler **not** a hosted API.

## 1. Install the patch

From your **`fantasy_football` repo root**:

```bash
git status --short
unzip -o ~/Downloads/fantasy_coach_ios_v8_0.zip
uv run python -m unittest discover -s tests -p test_ios_mobile.py -v
```

Review `git diff`. This package replaces `app/api.py` with the v7.27-compatible version plus the mobile access guard; if you have since edited it yourself, inspect the diff before committing.

## 2. Install the iOS dependencies on your Mac

Install current Xcode from the Mac App Store (Xcode 16+ for Capacitor 7), and make sure Xcode command-line tools are selected in Xcode Settings > Locations. Use a modern supported Node.js version.

```bash
cd web
npm install
npm run build
npm run ios:add
npm run ios:assets
npm run ios:open
```

- `ios:add` generates `web/ios/` (native Swift/Xcode project) using Swift Package Manager.
- `ios:assets` installs the native trophy icon and an iOS local-network permission into the generated project.
- `ios:open` opens Xcode.
- In Xcode, choose the **App** target, open **Signing & Capabilities**, check **Automatically manage signing**, and select your Apple **Personal Team**.
- Choose an iPhone simulator, press **Run** (▶). To test on your own iPhone, connect/trust it, select the device, enable Developer Mode if prompted, and press **Run**.
- In Xcode **General**, set a unique bundle identifier only if you encounter a conflict (`app.fantasycoach.personal` is included by default).

For later React changes, rerun from `web/`:

```bash
npm run ios:sync
npm run ios:assets
npm run ios:open
```

Native iOS project files under `web/ios/` are generated on your Mac; check in these files to Git if you want your Xcode/signing changes tracked. Do **not** commit Xcode personal signing credentials, API keys, `.env`, or the build output.

## 3. Start the Python backend for testing

**Mac / iOS Simulator:** From the repository root in another terminal:

```bash
export FANTASY_MOBILE_ACCESS_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf 'Mobile access key: %s\n' "$FANTASY_MOBILE_ACCESS_KEY"
uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

In the simulator's Fantasy Coach connection screen, enter:

- **Backend URL:** `http://127.0.0.1:8000`
- **Mobile API access key:** the `FANTASY_MOBILE_ACCESS_KEY` you printed.

The app verifies `/api/health` before showing your existing roster, waivers, lineups, player profiles, news, and chat. When your iPhone app is relaunched, it may ask for the mobile key again; the key is stored only for the current webview session, not permanently.

### Real iPhone over private Wi-Fi (development only)

On a network that **you own or trust** (not campus/public Wi-Fi), run the Mac backend with `--host 0.0.0.0`, keeping `FANTASY_MOBILE_ACCESS_KEY` set. Find the Mac LAN IP using `ipconfig getifaddr en0`. On the iPhone, enter `http://YOUR-MAC-IP:8000`, check **Allow private-LAN HTTP for development only**, and supply the access key.

iOS App Transport Security allows local networking through the `ios:assets` setup, but local routing and firewall behavior depend on the device and OS. If plain HTTP is blocked, use a private HTTPS endpoint instead. **Plain HTTP can expose the mobile access key to others on the network**; private HTTPS is strongly recommended. The FastAPI guard denies non-loopback API traffic unless the long access key matches (and fails closed if no key is configured). This is **not** a replacement for TLS or network isolation.

## 4. Access from your phone with your Mac switched off

The frontend is installed on your iPhone, but it **cannot** load live data when its API is down. You need an independently hosted FastAPI server with HTTPS and persistent data, for example a free/low-cost always-on host if one meets your usage needs. The GitHub Actions workflow only handles scheduled email delivery. Do not deploy your backend publicly without HTTPS, authentication, and careful control of ESPN/private keys.

## 5. Cost, publishing, and limitations

- Personal simulator testing and building with Xcode costs **$0**.
- With a free Apple Personal Team you can install on your own iPhone, but signing profiles expire after **7 days**, meaning you need to rebuild/reinstall periodically. A paid Apple Developer membership is needed for ordinary App Store distribution.
- This is an iOS **Capacitor webview app**, reusing the existing React screens and native iOS project—not a ground-up SwiftUI rewrite.
- It currently supports the existing browser app features, not native push notifications, widgets, Live Activities, or offline ESPN/Claude data.
- The iOS build and live ESPN interaction cannot be verified in this Linux environment. The provided offline tests cover mobile access, security headers, and wiring; Xcode compilation/device tests are the next validation.

## Troubleshooting

- **Connection refused:** Mac backend not running, wrong URL/IP, backend bound only to 127.0.0.1 for a physical iPhone, firewall, or phone on a different network.
- **401 Unauthorized:** The iPhone key differs from `FANTASY_MOBILE_ACCESS_KEY`, or it is not exported in the backend's terminal.
- **iOS ATS / networking error:** Prefer HTTPS and check `web/ios/App/App/Info.plist` has `NSAppTransportSecurity > NSAllowsLocalNetworking` set to true.
- **Xcode can't sign:** Choose your Apple account's Personal Team and a unique bundle ID.
- **Xcode project not found:** `npm run ios:add` must run once before `npm run ios:open`.
- **Changes aren't appearing:** `npm run ios:sync` copies the current React/Vite build into the native app.
- **No emails appearing within app:** GitHub Actions email remains separate; this patch doesn't add iOS push notifications.
