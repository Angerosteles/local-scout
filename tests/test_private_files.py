"""Local state is private: directories 0700, files 0600 (Unix)."""
import os
from pathlib import Path
import stat
import tempfile
import unittest

from plugin.scout import insights, storage


@unittest.skipIf(os.name != 'posix', 'Unix permissions')
class PrivateFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'plugin-data' / 'local-scout'
        self.old = os.umask(0o022)

    def tearDown(self):
        os.umask(self.old)
        self.temp.cleanup()

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_state_files_and_directories_are_private(self):
        storage.write_json(self.root / 'settings.json', {'mode': 'report'})
        storage.atomic(self.root / 'setup-triage' / 'support.jsonl', b'{"text": "x"}\n')
        insights.append_event(self.root, {'ts': 1, 'kind': 'tool', 'tool': 'scout_report', 'ok': 1})
        for path in (self.root / 'settings.json', self.root / 'setup-triage' / 'support.jsonl', self.root / insights.JOURNAL):
            self.assertEqual(self.mode(path), 0o600, path)
        for path in (self.root, self.root / 'setup-triage'):
            self.assertEqual(self.mode(path), 0o700, path)


if __name__ == '__main__':
    unittest.main()
