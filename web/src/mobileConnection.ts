import { Capacitor } from '@capacitor/core'

const API_URL_KEY = 'fantasy_coach_ios_api_url_v1'
const API_TOKEN_KEY = 'fantasy_coach_ios_api_token_session_v1'

export function isIOSApp(): boolean {
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'ios'
}

export function readAPIURL(): string {
  try { return localStorage.getItem(API_URL_KEY) || '' } catch { return '' }
}
export function readAPIToken(): string {
  try { return sessionStorage.getItem(API_TOKEN_KEY) || '' } catch { return '' }
}
export function connectionReady(): boolean {
  return Boolean(readAPIURL() && readAPIToken())
}

/** Only localhost is permitted over plaintext without an explicit LAN-dev opt-in. */
export function normalizeAPIURL(raw: string, allowLANHTTP = false): string {
  let url: URL
  try { url = new URL(raw.trim()) } catch { throw new Error('Enter a complete API URL, such as https://your-server.example') }
  if (url.username || url.password || url.search || url.hash) throw new Error('Use a server URL without credentials or query strings.')
  if (!['https:', 'http:'].includes(url.protocol)) throw new Error('Only HTTPS (or local HTTP testing) is supported.')
  if (url.pathname !== '/' && url.pathname !== '') throw new Error('Use the server base address, without /api or other paths.')
  if (url.protocol === 'http:') {
    const hostname = url.hostname.replace(/^\[|\]$/g, '')
    const loopback = ['localhost', '127.0.0.1', '::1'].includes(hostname)
    const lan = /^192\.168\.\d{1,3}\.\d{1,3}$/.test(hostname) || /^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(hostname) || /^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$/.test(hostname)
    if (!(loopback || (allowLANHTTP && lan))) throw new Error('Use HTTPS. Plain HTTP is only available for localhost or explicit private-LAN testing.')
  }
  return url.origin
}

export function saveAPIConnection(url: string, token: string): void {
  if (token.trim().length < 16) throw new Error('Enter the private mobile API access key (at least 16 characters).')
  localStorage.setItem(API_URL_KEY, url)
  // Token is deliberately session-only rather than written permanently to
  // localStorage. To persist securely across launches, add an iOS Keychain plugin.
  sessionStorage.setItem(API_TOKEN_KEY, token.trim())
  window.dispatchEvent(new Event('fantasycoach-mobile-connected'))
}

export function clearAPIConnection(): void {
  localStorage.removeItem(API_URL_KEY)
  sessionStorage.removeItem(API_TOKEN_KEY)
  window.dispatchEvent(new Event('fantasycoach-mobile-connected'))
}

export function apiURL(path: string): string {
  if (!isIOSApp()) return path // Existing Vite /api proxy stays unchanged.
  const base = readAPIURL()
  if (!base) throw new Error('Connect this iPhone to your Fantasy Coach API first.')
  if (!path.startsWith('/api/')) throw new Error('Only Fantasy Coach API paths are allowed.')
  return base + path
}

export function apiAuthHeaders(): Record<string, string> {
  if (!isIOSApp()) return {}
  const token = readAPIToken()
  return token ? { 'X-Fantasy-Mobile-Key': token } : {}
}
