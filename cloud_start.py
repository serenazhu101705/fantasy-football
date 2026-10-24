"""Start password-protected Fantasy Coach on a free stateless web host.

GitHub Actions handles recurring emails independently; this entrypoint
never launches the local email scheduler. Files under /tmp are ephemeral.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main():
    username = os.environ.get('FANTASY_WEB_USERNAME', '')
    password = os.environ.get('FANTASY_WEB_PASSWORD', '')
    if not username or ':' in username or len(password) < 18:
        raise SystemExit('Set FANTASY_WEB_USERNAME and strong FANTASY_WEB_PASSWORD (18+ characters).')

    # A private owner-only site. Keep ESPN writes and local digest scheduler off.
    os.environ['ESPN_WRITES_ENABLED'] = '0'
    os.environ['ESPN_AUTO_SWAPS_ENABLED'] = '0'
    os.environ['FANTASY_EMAIL_SCHEDULER_ENABLED'] = '0'
    os.environ.setdefault('FANTASY_SUBSCRIPTIONS_DB', '/tmp/fantasy_coach_subscriptions.sqlite3')
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'app'))

    import uvicorn
    uvicorn.run('deploy_api:app', host='0.0.0.0', port=int(os.getenv('PORT', '10000')), workers=1)


if __name__ == '__main__':
    main()
