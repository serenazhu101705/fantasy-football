import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RenderPwaFilesTests(unittest.TestCase):
    def test_manifest_and_icons_exist(self):
        manifest = json.loads((ROOT / 'web/public/manifest.webmanifest').read_text())
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['start_url'], '/')
        for name in ['apple-touch-icon.png', 'icon-192.png', 'icon-512.png']:
            self.assertTrue((ROOT / 'web/public' / name).is_file())

    def test_document_manifest(self):
        html = (ROOT / 'web/index.html').read_text()
        self.assertIn('manifest.webmanifest', html)
        self.assertIn('apple-touch-icon.png', html)


if __name__ == '__main__':
    unittest.main()
