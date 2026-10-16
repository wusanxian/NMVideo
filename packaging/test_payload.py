"""No user settings/databases in distribution; updates preserve runtime state."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load(name, filename):
    import sys
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PayloadTests(unittest.TestCase):
    def test_build_excludes_user_state(self):
        builder = load('payload_builder_test', 'build.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = root / 'backend'
            backend.mkdir()
            for name in builder.BACKEND_SKIP_FILES:
                (backend / name).write_text('private')
            (backend / 'app.py').write_text('source')
            dist = root / 'frontend/dist'
            dist.mkdir(parents=True)
            (dist / 'index.html').write_text('ui')
            with patch.object(builder, 'PROJECT', root), patch.object(builder, 'PACKAGING', root / 'packaging'):
                builder.assemble_payload()
            copied = root / 'packaging/payload/backend'
            self.assertEqual([p.name for p in copied.iterdir()], ['app.py'])

    def test_launcher_sync_preserves_user_settings_and_database(self):
        launcher = load('payload_launcher_test', 'launcher.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source', root / 'target'
            source.mkdir()
            target.mkdir()
            for name in ('settings.json', 'aivideo.db', 'aivideo.db-wal', 'aivideo.db-shm'):
                (source / name).write_text('bundled')
                (target / name).write_text('user-owned')
            (source / 'app.py').write_text('new source')
            launcher._copy_tree(source, target)
            self.assertEqual((target / 'app.py').read_text(), 'new source')
            for name in ('settings.json', 'aivideo.db', 'aivideo.db-wal', 'aivideo.db-shm'):
                self.assertEqual((target / name).read_text(), 'user-owned')


if __name__ == '__main__':
    unittest.main()
