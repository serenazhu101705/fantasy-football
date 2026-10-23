"""Offline contract checks for the Vite favicon and ESPN profile photos."""
from pathlib import Path
import re
import unittest
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1] / 'web'


class PlayerPresentationTests(unittest.TestCase):
    def test_favicon_is_valid_svg_and_vite_served(self):
        svg = ROOT / 'public/favicon.svg'
        self.assertTrue(svg.is_file())
        root = ElementTree.parse(svg).getroot()
        self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
        self.assertIn('trophy', (ROOT / 'src/App.tsx').read_text().lower())
        html = (ROOT / 'index.html').read_text()
        self.assertRegex(html, r'<link\s+rel="icon"[^>]*href="/favicon\.svg\?v=724"')

    def test_espn_headshot_url_uses_valid_athlete_id_only(self):
        source = (ROOT / 'src/PlayerHeadshot.tsx').read_text()
        self.assertIn('Number.isSafeInteger(id)', source)
        self.assertIn('id > 0', source)
        self.assertIn('https://a.espncdn.com/i/headshots/nfl/players/full/${id}.png', source)
        self.assertIn('onError={() => setFailed(true)}', source)
        self.assertIn('useEffect(() => setFailed(false), [id])', source)
        self.assertIn('player-headshot-fallback', source)

    def test_ids_propagate_to_row_and_profile_header(self):
        source = (ROOT / 'src/App.tsx').read_text()
        self.assertIn('id={player.espn_id} name={player.name}', source)
        self.assertIn('id={selected.id} name={profile?.name || selected.name} size="lg"', source)
        self.assertIn('import { PlayerHeadshot }', source)

    def test_responsive_styles_present(self):
        source = (ROOT / 'src/styles.css').read_text()
        for rule in ('.player-headshot-sm', '.player-headshot-lg', '.drawer-player-identity', '@media(max-width:450px)'):
            self.assertIn(rule, source)


if __name__ == '__main__':
    unittest.main()
