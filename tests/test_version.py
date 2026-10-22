import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import version


def stamp(value):
    return str(int(datetime.fromisoformat(value).timestamp()))


class VersionTests(unittest.TestCase):
    def test_shanghai_day_and_daily_sequence(self):
        self.assertEqual(version.version_from_timestamps([
            stamp('2026-10-22T08:00:00+00:00'),
            stamp('2026-10-21T16:00:00+00:00'),
            stamp('2026-10-21T15:59:59+00:00'),
        ]), '26.1022.2')
        self.assertEqual(version.version_from_timestamps([
            stamp('2027-01-04T16:00:00+00:00'),
            stamp('2027-01-04T15:59:59+00:00'),
        ]), '27.0105.1')

    def test_running_server_observes_changed_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.git').mkdir()
            with patch.object(version,'ROOT',root), patch.object(version,'git',side_effect=['false','old','false','new']), patch.object(version,'version_at_commit',side_effect=['26.1022.1','26.1022.2']):
                self.assertEqual(version.get_version(),'26.1022.1')
                self.assertEqual(version.get_version(),'26.1022.2')

    def test_package_uses_frozen_version_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'app').mkdir()
            (root/'app/version.txt').write_text('26.1022.3\n',encoding='utf-8')
            with patch.object(version,'ROOT',root), patch.object(version,'git') as git:
                self.assertEqual(version.get_version(),'26.1022.3')
                git.assert_not_called()

    def test_shallow_history_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.git').mkdir()
            with patch.object(version,'ROOT',root), patch.object(version,'git',return_value='true'):
                with self.assertRaisesRegex(RuntimeError,'full Git history'):
                    version.get_version()
