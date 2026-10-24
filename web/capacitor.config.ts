import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'app.fantasycoach.personal',
  appName: 'Fantasy Coach',
  webDir: 'dist',
  ios: {
    contentInset: 'automatic',
  },
  // The native iOS bridge can make API requests to an explicitly configured
  // server. No ESPN cookies, Resend key or Anthropic key are bundled in iOS.
  plugins: { CapacitorHttp: { enabled: true } },
}

export default config
