import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.media_tools import FFProbeError, ensure_ffprobe_available, ffprobe_version, resolve_ffprobe_path


class MediaToolsTests(unittest.TestCase):
    def fake_executable(self, root, name='ffprobe.cmd'):
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('@echo ffprobe version fixture\n', encoding='utf-8')
        return path

    def test_explicit_config_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'New project 10'
            (root / 'config').mkdir(parents=True)
            executable = self.fake_executable(root / 'tools')
            (root / 'config' / 'media_tools.json').write_text(
                json.dumps({'ffprobe_path': str(executable)}), encoding='utf-8'
            )
            self.assertEqual(resolve_ffprobe_path(root), executable.resolve())

    def test_env_path_has_priority_over_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured = self.fake_executable(root, 'configured.cmd')
            explicit = self.fake_executable(root, 'explicit.cmd')
            (root / 'config').mkdir()
            (root / 'config' / 'media_tools.json').write_text(
                json.dumps({'ffprobe_path': str(configured)}), encoding='utf-8'
            )
            with patch.dict('os.environ', {'FFPROBE_PATH': str(explicit)}):
                self.assertEqual(resolve_ffprobe_path(root), explicit.resolve())

    def test_path_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = self.fake_executable(Path(directory))
            with patch('src.media_tools.shutil.which', return_value=str(executable)):
                self.assertEqual(resolve_ffprobe_path(Path(directory)), executable.resolve())

    def test_missing_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config' / 'media_tools.json').write_text(
                json.dumps({'ffprobe_path': str(root / 'missing.exe')}), encoding='utf-8'
            )
            with self.assertRaisesRegex(FFProbeError, 'nao encontrado'):
                ensure_ffprobe_available(root)

    def test_nonzero_exit_is_rejected(self):
        with patch('src.media_tools.subprocess.run') as run:
            run.return_value.returncode = 2
            with self.assertRaisesRegex(FFProbeError, 'exit code 2'):
                ffprobe_version(Path('C:/tools/ffprobe.exe'))
            run.assert_called_once()
            self.assertEqual(Path(run.call_args.args[0][0]), Path('C:/tools/ffprobe.exe'))

    def test_timeout_is_rejected(self):
        import subprocess
        with patch('src.media_tools.subprocess.run', side_effect=subprocess.TimeoutExpired('ffprobe', 1)):
            with self.assertRaisesRegex(FFProbeError, 'timeout'):
                ffprobe_version(Path('C:/tools/ffprobe.exe'))

    def test_permission_error_keeps_sanitized_process_diagnostics(self):
        error = PermissionError(13, 'denied')
        error.winerror = 5
        with patch('src.media_tools.subprocess.run', side_effect=error):
            with self.assertRaises(FFProbeError) as raised:
                ffprobe_version(Path('C:/tools/ffprobe.exe'))
        self.assertEqual(raised.exception.exception_type, 'PermissionError')
        self.assertEqual(raised.exception.winerror, 5)
        self.assertFalse(raised.exception.process_started)


if __name__ == '__main__':
    unittest.main()
