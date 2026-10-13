import contextlib
import io
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from app import db as storage
from app import container_entrypoint as entry


class ContainerStartupTests(unittest.TestCase):
    def test_empty_database_password_generated_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'app.db'
            with patch.object(storage, 'DB_PATH', path), patch.dict(os.environ, {'ADMIN_PASSWORD': '', 'ADMIN_USERNAME': 'admin'}):
                output = io.StringIO()
                with patch.object(entry.secrets, 'token_urlsafe', return_value='Synthetic-password-2026'), contextlib.redirect_stdout(output):
                    entry.initialize()
                self.assertIn('Synthetic-password-2026', output.getvalue())
                with contextlib.closing(sqlite3.connect(path)) as c:
                    before = c.execute('SELECT username,password_hash FROM users').fetchone()
                os.environ['ADMIN_PASSWORD'] = ''
                output = io.StringIO()
                with patch.object(entry.secrets, 'token_urlsafe') as generate, contextlib.redirect_stdout(output):
                    entry.initialize()
                    generate.assert_not_called()
                self.assertEqual(output.getvalue(), '')
                with contextlib.closing(sqlite3.connect(path)) as c:
                    self.assertEqual(c.execute('SELECT username,password_hash FROM users').fetchone(), before)

    def test_supplied_password_not_printed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(storage, 'DB_PATH', Path(tmp)/'app.db'), patch.dict(os.environ, {'ADMIN_PASSWORD': 'Configured-test-password', 'ADMIN_USERNAME': 'admin'}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                entry.initialize()
            self.assertEqual(output.getvalue(), '')

    def test_permissions_repaired_only_in_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'data'
            folder.mkdir()
            backup = folder/'backups'
            backup.mkdir()
            file = backup/'migration.db'
            file.write_text('synthetic')
            outside = Path(tmp)/'outside.txt'
            outside.write_text('unchanged')
            with patch.object(os, 'chown', create=True) as chown:
                entry.prepare_data(folder, 10001, 10001)
                targets = {call.args[0] for call in chown.call_args_list}
                self.assertEqual(targets, {folder, backup, file})
            self.assertEqual(outside.read_text(), 'unchanged')
            self.assertTrue(file.stat().st_mode & stat.S_IWUSR)

    def test_drop_privileges_before_maintenance_command(self):
        account = SimpleNamespace(pw_uid=10001, pw_gid=10001)
        pwd = SimpleNamespace(getpwnam=lambda name:account)
        calls = Mock()
        with patch.dict(sys.modules, {'pwd':pwd}), patch.object(sys, 'argv', ['entry','python','-m','app.manage','restore','/app/data/migration.db','--service-stopped']), patch.object(os, 'geteuid', return_value=0, create=True), patch.object(os, 'setgroups', create=True) as groups, patch.object(os, 'setgid', create=True) as gid, patch.object(os, 'setuid', create=True) as uid, patch.object(os, 'execvp') as execute, patch.object(entry, 'prepare_data') as prepare, patch.object(entry, 'initialize') as initialize:
            calls.attach_mock(prepare,'prepare');calls.attach_mock(groups,'groups');calls.attach_mock(gid,'gid');calls.attach_mock(uid,'uid');calls.attach_mock(execute,'execute')
            entry.main()
            self.assertEqual([call[0] for call in calls.mock_calls],['prepare','groups','gid','uid','execute'])
            initialize.assert_not_called()
            execute.assert_called_once_with('python',['python','-m','app.manage','restore','/app/data/migration.db','--service-stopped'])


if __name__ == '__main__':
    unittest.main()
