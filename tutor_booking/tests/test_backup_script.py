import gzip
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from django.test import SimpleTestCase


def _find_bash() -> str:
    """Find appropriate bash executable across environments."""
    if os.name == 'nt':
        # Prefer Git Bash on Windows so MSYS paths like /f/... resolve correctly
        candidates = [
            Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'bin' / 'bash.exe',
            Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'usr' / 'bin' / 'bash.exe',
            Path(os.environ.get('LOCALAPPDATA', r'C:\Users\Default\AppData\Local')) / 'Programs' / 'Git' / 'bin' / 'bash.exe',
        ]
        for c in candidates:
            if c.exists():
                return str(c)
    which_bash = shutil.which('bash')
    return which_bash if which_bash else 'bash'


def _to_posix_path(p: Path) -> str:
    """Convert a path to POSIX/MSYS style path for bash."""
    path_str = str(p)
    if os.name == 'nt':
        try:
            res = subprocess.run(['cygpath', '-u', path_str], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except FileNotFoundError:
            pass
    return p.as_posix()


class BackupScriptTests(SimpleTestCase):
    """Tests for scripts/backup.sh dual-artifact backup script."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project_root = Path(__file__).resolve().parent.parent.parent
        cls.script_path = cls.project_root / 'scripts' / 'backup.sh'
        cls.bash_bin = _find_bash()
        if not cls.script_path.exists():
            raise FileNotFoundError(f"backup.sh not found at {cls.script_path}")

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp(prefix='backup_test_')
        self.backup_dir = Path(self.temp_dir) / 'backups'
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir = Path(self.temp_dir) / 'media'
        self.media_dir.mkdir(parents=True, exist_ok=True)
        (self.media_dir / 'avatar.jpg').write_bytes(b'fake_image_bytes_12345')

        # Setup mock bin directory for docker
        self.mock_bin_dir = Path(self.temp_dir) / 'bin'
        self.mock_bin_dir.mkdir(parents=True, exist_ok=True)
        self._create_mock_docker(exit_code=0, sql_content="-- Mock PostgreSQL Dump\nCREATE TABLE t (id int);\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        super().tearDown()

    def _create_aged_artifact(self, filename: str, age_seconds: float) -> Path:
        """Helper to create a test backup file backdated by age_seconds."""
        file_path = self.backup_dir / filename
        file_path.write_bytes(b'dummy_snapshot_content')
        target_time = time.time() - age_seconds
        os.utime(file_path, (target_time, target_time))
        return file_path

    def _create_mock_docker(self, exit_code=0, sql_content="-- Mock PostgreSQL Dump\nCREATE TABLE t (id int);\n"):
        docker_path = self.mock_bin_dir / 'docker'
        script = f"""#!/usr/bin/env bash
if [[ "$1" == "ps" ]]; then
    if [[ "${{MOCK_DOCKER_RUNNING_CONTAINERS:-unset}}" != "unset" ]]; then
        echo -e "${{MOCK_DOCKER_RUNNING_CONTAINERS}}"
    else
        echo "tutor_booking_db"
        echo "tutor_booking_db_prod"
    fi
elif [[ "$1" == "exec" ]]; then
    if [[ {exit_code} -ne 0 ]]; then
        echo "pg_dump: error: simulated failure" >&2
        exit {exit_code}
    fi
    cat << 'EOF'
{sql_content}
EOF
else
    exit 0
fi
"""
        with open(docker_path, 'w', newline='\n', encoding='utf-8') as f:
            f.write(script)
        os.chmod(docker_path, 0o755)
        # On Windows Git Bash, ensure MSYS permission mode recognizes executable
        if os.name == 'nt' and hasattr(self, 'bash_bin'):
            subprocess.run([self.bash_bin, '-c', f'chmod +x {_to_posix_path(docker_path)}'], capture_output=True)

    def _run_script(self, args, extra_env=None):
        env = os.environ.copy()
        # Prepend mock bin to PATH (both native and POSIX format for bash compatibility)
        posix_bin = _to_posix_path(self.mock_bin_dir)
        env['PATH'] = f"{posix_bin}{os.pathsep}{str(self.mock_bin_dir)}{os.pathsep}{env.get('PATH', '')}"
        # Set DOCKER_BIN explicitly to guarantee the mock executable is used
        env['DOCKER_BIN'] = _to_posix_path(self.mock_bin_dir / 'docker')
        # Default MEDIA_DIR to mock media dir
        env['MEDIA_DIR'] = _to_posix_path(self.media_dir)
        if extra_env:
            env.update(extra_env)

        cmd = [
            self.bash_bin,
            './scripts/backup.sh',
            '--backup-dir',
            _to_posix_path(self.backup_dir),
        ] + args

        return subprocess.run(
            cmd,
            cwd=str(self.project_root),
            env=env,
            capture_output=True,
            text=True,
        )

    def test_help_flag_displays_usage_and_exits_zero(self):
        cmd = [self.bash_bin, './scripts/backup.sh', '--help']
        result = subprocess.run(cmd, cwd=str(self.project_root), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('Dual-artifact backup script', result.stdout)
        self.assertIn('--env', result.stdout)
        self.assertIn('--backup-dir', result.stdout)
        self.assertIn('--retention-days', result.stdout)
        self.assertIn('--skip-media', result.stdout)
        self.assertEqual(result.stderr.strip(), '')

    def test_unknown_option_fails_with_exit_code_1(self):
        result = self._run_script(['--unrecognized-option'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('Unknown option: --unrecognized-option', result.stderr)

    def test_invalid_env_option_fails_with_exit_code_1(self):
        result = self._run_script(['--env', 'staging'])
        self.assertEqual(result.returncode, 1)
        self.assertIn("Invalid environment 'staging'", result.stderr)

    def test_invalid_retention_days_fails_with_exit_code_1(self):
        result = self._run_script(['--retention-days', 'invalid'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('Retention days must be a non-negative integer', result.stderr)

    def test_missing_container_fails_with_error(self):
        result = self._run_script(
            ['--env', 'local'],
            extra_env={
                'CONTAINER_NAME': 'nonexistent_test_container_999',
                'MOCK_DOCKER_RUNNING_CONTAINERS': 'other_container_1\nother_container_2',
            }
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Database container 'nonexistent_test_container_999' is not running", result.stderr)

    def test_successful_backup_creates_coupled_archives(self):
        result = self._run_script(['--env', 'local'])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}")
        self.assertIn('Backup Completed Successfully', result.stdout)
        self.assertIn('[OK] Database backup verified', result.stdout)
        self.assertIn('[OK] Media archive verified', result.stdout)

        # Verify artifacts
        db_files = list(self.backup_dir.glob('backup_*_db.sql.gz'))
        media_files = list(self.backup_dir.glob('backup_*_media.tar.gz'))

        self.assertEqual(len(db_files), 1)
        self.assertEqual(len(media_files), 1)

        # Check timestamp prefix match
        db_prefix = db_files[0].name.replace('_db.sql.gz', '')
        media_prefix = media_files[0].name.replace('_media.tar.gz', '')
        self.assertEqual(db_prefix, media_prefix)

        # Validate database gzip content
        with gzip.open(db_files[0], 'rt', encoding='utf-8') as gz_f:
            content = gz_f.read()
            self.assertIn('Mock PostgreSQL Dump', content)

        # Validate media tar content
        with tarfile.open(media_files[0], 'r:gz') as tar_f:
            names = tar_f.getnames()
            self.assertTrue(any('avatar.jpg' in n for n in names))

    def test_skip_media_creates_only_database_snapshot(self):
        result = self._run_script(['--env', 'local', '--skip-media'])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Skipping media archiving', result.stdout)

        db_files = list(self.backup_dir.glob('backup_*_db.sql.gz'))
        media_files = list(self.backup_dir.glob('backup_*_media.tar.gz'))

        self.assertEqual(len(db_files), 1)
        self.assertEqual(len(media_files), 0)

    def test_missing_media_directory_without_skip_media_fails(self):
        shutil.rmtree(self.media_dir)
        result = self._run_script(['--env', 'local'])
        self.assertEqual(result.returncode, 1, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn("Media directory", result.stderr)
        self.assertIn("does not exist", result.stderr)

    def test_pg_dump_failure_cleans_up_incomplete_artifacts(self):
        self._create_mock_docker(exit_code=1)
        result = self._run_script(['--env', 'local'])

        self.assertEqual(result.returncode, 1, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Cleaning up incomplete artifacts', result.stderr)

        # Ensure no partial files remain
        remaining = list(self.backup_dir.glob('backup_*'))
        self.assertEqual(remaining, [])

    def test_retention_pruning_removes_old_archives_and_retains_recent(self):
        ten_days = 10 * 86400
        two_days = 2 * 86400

        # Old files (should be pruned)
        old_db = self._create_aged_artifact('backup_20260918_120000_db.sql.gz', ten_days)
        old_media = self._create_aged_artifact('backup_20260918_120000_media.tar.gz', ten_days)

        # Recent file (should be kept)
        recent_db = self._create_aged_artifact('backup_20260926_120000_db.sql.gz', two_days)

        # Unrelated file (should be untouched)
        notes = self._create_aged_artifact('custom_notes.txt', ten_days)

        result = self._run_script(['--env', 'local', '--retention-days', '7'])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Pruned 2 artifact(s) older than 7 days', result.stdout)

        # Check file presence
        self.assertFalse(old_db.exists())
        self.assertFalse(old_media.exists())
        self.assertTrue(recent_db.exists())
        self.assertTrue(notes.exists())

        # Also verify new backup was created
        new_db_files = [f for f in self.backup_dir.glob('backup_*_db.sql.gz') if f != recent_db]
        self.assertEqual(len(new_db_files), 1)

    def test_retention_days_zero_disables_pruning(self):
        old_db = self._create_aged_artifact('backup_20260918_120000_db.sql.gz', 10 * 86400)

        result = self._run_script(['--env', 'local', '--retention-days', '0'])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Retention pruning disabled', result.stdout)
        self.assertTrue(old_db.exists())

    def test_env_explicit_prod_resolution(self):
        result = self._run_script(['--env', 'prod'])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Environment:   prod (explicit)', result.stdout)
        self.assertIn('Container:     tutor_booking_db_prod', result.stdout)

    def test_env_auto_detection_resolves_local_when_no_prod_container(self):
        result = self._run_script(
            [],
            extra_env={'MOCK_DOCKER_RUNNING_CONTAINERS': 'tutor_booking_db\nsome_other_app'}
        )
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Environment:   local (auto-detected)', result.stdout)
        self.assertIn('Container:     tutor_booking_db', result.stdout)

    def test_env_auto_detection_resolves_prod_when_prod_container_running(self):
        result = self._run_script(
            [],
            extra_env={'MOCK_DOCKER_RUNNING_CONTAINERS': 'tutor_booking_db_prod'}
        )
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Environment:   prod (auto-detected)', result.stdout)
        self.assertIn('Container:     tutor_booking_db_prod', result.stdout)
