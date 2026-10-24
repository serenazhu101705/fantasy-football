"""Install native icon and local-network permission after adding iOS platform."""
from __future__ import annotations
import json
import plistlib
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PLIST = ROOT / 'ios/App/App/Info.plist'
ASSET = ROOT / 'ios/App/App/Assets.xcassets/AppIcon.appiconset'
ICON = ROOT / 'ios-assets/AppIcon-1024.png'


def main():
    if not PLIST.exists():
        raise SystemExit('No Xcode project yet. Run `npm run ios:add` from web/ first.')
    with PLIST.open('rb') as f:
        info = plistlib.load(f)
    info['NSLocalNetworkUsageDescription'] = 'Connect to your private Fantasy Coach API on the local network.'
    ats = info.setdefault('NSAppTransportSecurity', {})
    ats['NSAllowsLocalNetworking'] = True
    # Do NOT disable App Transport Security globally.
    with PLIST.open('wb') as f:
        plistlib.dump(info, f, sort_keys=False)
    ASSET.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON, ASSET / ICON.name)
    (ASSET/'Contents.json').write_text(json.dumps({
        'images': [{'filename':ICON.name,'idiom':'universal','platform':'ios','size':'1024x1024'}],
        'info': {'author':'xcode','version':1},
    }, indent=2)+'\n')
    print('Installed trophy home-screen icon and iOS local-network permission.')
    print('For production, use HTTPS; local HTTP is for private testing only.')

if __name__=='__main__':
    main()
