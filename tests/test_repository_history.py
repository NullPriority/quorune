from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import validate_repository as repository


class RepositoryHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '--quiet', '--initial-branch=checked')
        self.git('config', 'user.name', 'Repository fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgSign', 'false')
        self.git('config', 'core.hooksPath', str(self.root/'no-hooks'))
        (self.root/'source.py').write_text('value = 1\n', encoding='utf-8')
        self.commit('Create checked source')
        self.patch = patch.object(repository, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root,
            text=True, encoding='utf-8', stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git('add', '--force', '--all')
        self.git('commit', '--quiet', '-m', message)

    def oversized(self):
        path = self.root/'oversized.json'
        with path.open('wb') as stream:
            stream.seek(10*1024*1024)
            stream.write(b'0')
        return path

    def test_unrelated_branch_artifacts_cannot_change_checked_revision_result(self):
        before = repository.validate_tracked_files()
        self.git('checkout', '--quiet', '-b', 'unrelated')
        self.oversized()
        path = self.root/'local'/'cache.json'
        path.parent.mkdir()
        path.write_text('{}\n', encoding='utf-8')
        self.commit('Add unrelated forbidden and oversized artifacts')
        self.git('checkout', '--quiet', 'checked')
        self.assertEqual(before, repository.validate_tracked_files())

    def test_deleted_oversized_blob_in_checked_ancestry_still_fails(self):
        path = self.oversized()
        self.commit('Add oversized history')
        path.unlink()
        self.commit('Remove oversized current file')
        with self.assertRaisesRegex(ValueError, 'historical blob exceeds 10 MiB: oversized.json'):
            repository.validate_tracked_files()

    def test_deleted_forbidden_path_in_checked_ancestry_still_fails(self):
        path = self.root/'local'/'cache.json'
        path.parent.mkdir()
        path.write_text('{}\n', encoding='utf-8')
        self.commit('Add forbidden history')
        path.unlink()
        self.commit('Remove forbidden current file')
        with self.assertRaisesRegex(ValueError, 'forbidden artifact remains in history: local/cache.json'):
            repository.validate_tracked_files()

    def test_uncommitted_oversized_tracked_source_still_fails(self):
        self.oversized()
        self.git('add', 'oversized.json')
        with self.assertRaisesRegex(ValueError, 'tracked file exceeds 10 MiB: oversized.json'):
            repository.validate_tracked_files()


if __name__ == '__main__':
    unittest.main()
