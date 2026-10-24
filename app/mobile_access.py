"""Opt-in remote/mobile access guard for Fantasy Coach API.

Local Vite -> localhost:8000 requests remain unchanged. All non-loopback
remote clients must supply a long random FANTASY_MOBILE_ACCESS_KEY and the
matching X-Fantasy-Mobile-Key header. This is NOT an alternative to HTTPS.
"""
from __future__ import annotations
import ipaddress
import os
import secrets
from fastapi import Request
from fastapi.responses import JSONResponse


def install_mobile_access_guard(api):
    @api.middleware('http')
    async def protect_remote_api(request: Request, call_next):
        if not request.url.path.startswith('/api/') or request.method == 'OPTIONS':
            return await call_next(request)
        # Preserve token-based confirmation and unsubscribe links opened from
        # email clients (which cannot supply the iOS API-key header).
        if request.url.path in {'/api/subscriptions/confirm', '/api/subscriptions/unsubscribe'}:
            return await call_next(request)
        client = request.client.host if request.client else ''
        try:
            local = ipaddress.ip_address(client).is_loopback
        except ValueError:
            local = client == 'localhost'
        if not local:
            expected = os.getenv('FANTASY_MOBILE_ACCESS_KEY', '').strip()
            provided = request.headers.get('X-Fantasy-Mobile-Key', '')
            if len(expected) < 32 or not secrets.compare_digest(expected, provided):
                return JSONResponse({'detail': 'Mobile API access denied.'}, status_code=401)
        return await call_next(request)
