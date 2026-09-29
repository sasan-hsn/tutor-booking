import gzip
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from django.test import SimpleTestCase


def _find_bash() -> str:
    """Find appropriate bash executable across environments."""
    if os.name == 'nt':
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


class RestoreScriptTests(SimpleTestCase):
    """Comprehensive test suite for scripts/restore.sh disaster recovery restore tooling."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project_root = Path(__file__).resolve().parent.parent.parent
        cls.script_path = cls.project_root / 'scripts' / 'restore.sh'
        cls.bash_bin = _find_bash()

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp(prefix='restore_test_')
        self.backup_dir = Path(self.temp_dir) / 'backups'
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.target_media_dir = Path(self.temp_dir) / 'media'
        self.target_media_dir.mkdir(parents=True, exist_ok=True)

        self.mock_bin_dir = Path(self.temp_dir) / 'bin'
        self.mock_bin_dir.mkdir(parents=True, exist_ok=True)
        self.docker_log_file = Path(self.temp_dir) / 'docker_calls.log'
        self.restored_sql_file = Path(self.temp_dir) / 'restored_stream.sql'

        self._create_mock_docker(exit_code=0)

        # Create valid sample db and media backup files
        self.valid_db_file = self.backup_dir / 'backup_20260929_120000_db.sql.gz'
        with gzip.open(self.valid_db_file, 'wt', encoding='utf-8') as f:
            f.write("-- PostgreSQL Test Dump\nCREATE TABLE users (id int);\n")

        self.valid_media_file = self.backup_dir / 'backup_20260929_120000_media.tar.gz'
        self._create_sample_media_tar(self.valid_media_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        super().tearDown()

    def _create_sample_media_tar(self, destination: Path, filenames=('avatar.jpg', 'nested/photo.png')):
        sample_dir = Path(self.temp_dir) / 'sample_media_staging'
        sample_dir.mkdir(parents=True, exist_ok=True)
        media_subdir = sample_dir / 'media'
        media_subdir.mkdir(parents=True, exist_ok=True)

        for fname in filenames:
            fpath = media_subdir / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_bytes(f'content_for_{fname}'.encode('utf-8'))

        with tarfile.open(destination, 'w:gz') as tar:
            tar.add(str(media_subdir), arcname='media')

    def _create_mock_docker(self, exit_code=0, psql_fail_on_restore=False):
        docker_path = self.mock_bin_dir / 'docker'
        log_path_posix = _to_posix_path(self.docker_log_file)
        restored_sql_posix = _to_posix_path(self.restored_sql_file)

        script = f"""#!/usr/bin/env bash
echo "$@" >> "{log_path_posix}"

if [[ "$1" == "ps" ]]; then
    if [[ "${{MOCK_DOCKER_RUNNING_CONTAINERS:-unset}}" != "unset" ]]; then
        echo -e "${{MOCK_DOCKER_RUNNING_CONTAINERS}}"
    else
        echo "tutor_booking_db"
        echo "tutor_booking_db_prod"
    fi
elif [[ "$1" == "exec" ]]; then
    if [[ {exit_code} -ne 0 ]]; then
        echo "docker: error: simulated failure" >&2
        exit {exit_code}
    fi

    # Check if psql restore from stdin
    is_stdin_restore=false
    for arg in "$@"; do
        if [[ "$arg" == "-i" ]]; then
            is_stdin_restore=true
            break
        fi
    done

    if [[ "$is_stdin_restore" == "true" ]]; then
        if [[ "{"true" if psql_fail_on_restore else "false"}" == "true" ]]; then
            echo "psql: error: syntax error in restore stream" >&2
            exit 2
        fi
        cat > "{restored_sql_posix}"
    fi
    exit 0
else
    exit 0
fi
"""
        with open(docker_path, 'w', newline='\n', encoding='utf-8') as f:
            f.write(script)
        os.chmod(docker_path, 0o755)
        if os.name == 'nt' and hasattr(self, 'bash_bin'):
            subprocess.run([self.bash_bin, '-c', f'chmod +x {_to_posix_path(docker_path)}'], capture_output=True)

    def _run_script(self, args, extra_env=None, input_text=None):
        env = os.environ.copy()
        posix_bin = _to_posix_path(self.mock_bin_dir)
        env['PATH'] = f"{posix_bin}{os.pathsep}{str(self.mock_bin_dir)}{os.pathsep}{env.get('PATH', '')}"
        env['DOCKER_BIN'] = _to_posix_path(self.mock_bin_dir / 'docker')
        env['MEDIA_DIR'] = _to_posix_path(self.target_media_dir)
        if extra_env:
            env.update(extra_env)

        cmd = [self.bash_bin, './scripts/restore.sh'] + args

        return subprocess.run(
            cmd,
            cwd=str(self.project_root),
            env=env,
            capture_output=True,
            text=True,
            input=input_text,
        )

    def test_help_flag_displays_usage_and_exits_zero(self):
        cmd = [self.bash_bin, './scripts/restore.sh', '--help']
        result = subprocess.run(cmd, cwd=str(self.project_root), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('Disaster recovery restore script', result.stdout)
        self.assertIn('--db-file', result.stdout)
        self.assertIn('--target-db', result.stdout)
        self.assertIn('--dangerously-restore-to-production', result.stdout)
        self.assertIn('--skip-media', result.stdout)
        self.assertEqual(result.stderr.strip(), '')

    def test_missing_db_file_arg_fails_with_exit_code_1(self):
        result = self._run_script([])
        self.assertEqual(result.returncode, 1)
        self.assertIn('--db-file is required', result.stderr)

    def test_nonexistent_db_file_fails_with_exit_code_1(self):
        result = self._run_script(['--db-file', _to_posix_path(self.backup_dir / 'nonexistent.sql.gz')])
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not exist or is empty', result.stderr)

    def test_corrupted_gzip_db_file_fails_integrity_before_any_db_drop(self):
        corrupted = self.backup_dir / 'corrupt_db.sql.gz'
        corrupted.write_bytes(b'NOT_A_VALID_GZIP_STREAM_CONTENT')

        result = self._run_script(['--db-file', _to_posix_path(corrupted)])
        self.assertEqual(result.returncode, 1)
        self.assertIn('failed gzip integrity check', result.stderr)

        # Verify no docker drop/create commands were executed
        if self.docker_log_file.exists():
            calls = self.docker_log_file.read_text(encoding='utf-8')
            self.assertNotIn('DROP DATABASE', calls)
            self.assertNotIn('CREATE DATABASE', calls)

    def test_unknown_option_fails_with_exit_code_1(self):
        result = self._run_script(['--db-file', _to_posix_path(self.valid_db_file), '--unknown-flag'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('Unknown option: --unknown-flag', result.stderr)

    def test_missing_container_fails_with_error(self):
        result = self._run_script(
            ['--db-file', _to_posix_path(self.valid_db_file)],
            extra_env={
                'CONTAINER_NAME': 'offline_container_999',
                'MOCK_DOCKER_RUNNING_CONTAINERS': 'other_container',
            }
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Database container 'offline_container_999' is not running", result.stderr)

    def test_defaults_to_tutor_booking_restore_test_target_database(self):
        result = self._run_script(['--db-file', _to_posix_path(self.valid_db_file)])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Target DB:     tutor_booking_restore_test', result.stdout)

        # Verify log recorded drop and create on tutor_booking_restore_test
        calls = self.docker_log_file.read_text(encoding='utf-8')
        self.assertIn('DROP DATABASE IF EXISTS "tutor_booking_restore_test"', calls)
        self.assertIn('CREATE DATABASE "tutor_booking_restore_test"', calls)

    def test_prod_db_restore_refused_without_dangerously_restore_flag(self):
        result = self._run_script([
            '--db-file', _to_posix_path(self.valid_db_file),
            '--target-db', 'tutor_booking_prod',
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn('Refusing to restore to production database', result.stderr)
        self.assertIn('--dangerously-restore-to-production', result.stderr)

        # Ensure no destructive actions were initiated
        if self.docker_log_file.exists():
            calls = self.docker_log_file.read_text(encoding='utf-8')
            self.assertNotIn('DROP DATABASE', calls)

    def test_prod_env_active_db_refused_without_dangerously_restore_flag(self):
        result = self._run_script(
            [
                '--db-file', _to_posix_path(self.valid_db_file),
                '--env', 'prod',
                '--target-db', 'custom_prod_db',
            ],
            extra_env={'POSTGRES_DB': 'custom_prod_db'}
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('Refusing to restore to production database', result.stderr)
        self.assertIn('--dangerously-restore-to-production', result.stderr)

    def test_prod_db_restore_aborted_when_confirmation_text_mismatches(self):
        result = self._run_script(
            [
                '--db-file', _to_posix_path(self.valid_db_file),
                '--target-db', 'tutor_booking_prod',
                '--dangerously-restore-to-production',
            ],
            input_text='wrong_confirmation_name\n'
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('Confirmation string did not match', result.stderr)

        if self.docker_log_file.exists():
            calls = self.docker_log_file.read_text(encoding='utf-8')
            self.assertNotIn('DROP DATABASE', calls)

    def test_prod_db_restore_succeeds_when_danger_flag_and_exact_confirmation_provided(self):
        result = self._run_script(
            [
                '--db-file', _to_posix_path(self.valid_db_file),
                '--target-db', 'tutor_booking_prod',
                '--dangerously-restore-to-production',
            ],
            input_text='tutor_booking_prod\n'
        )
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Restore Completed Successfully', result.stdout)

        calls = self.docker_log_file.read_text(encoding='utf-8')
        self.assertIn('DROP DATABASE IF EXISTS "tutor_booking_prod"', calls)
        self.assertIn('CREATE DATABASE "tutor_booking_prod"', calls)

    def test_companion_media_archive_auto_discovered_and_restored(self):
        result = self._run_script([
            '--db-file', _to_posix_path(self.valid_db_file),
            '--target-media-dir', _to_posix_path(self.target_media_dir),
        ])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Discovered companion media archive', result.stdout)
        self.assertIn('Media archive restored successfully', result.stdout)

        # Verify files were unpacked into target_media_dir
        self.assertTrue((self.target_media_dir / 'avatar.jpg').exists())
        self.assertTrue((self.target_media_dir / 'nested' / 'photo.png').exists())

    def test_skip_media_flag_bypasses_companion_media_restoration(self):
        # Companion media exists on disk, but --skip-media is passed
        result = self._run_script([
            '--db-file', _to_posix_path(self.valid_db_file),
            '--skip-media',
            '--target-media-dir', _to_posix_path(self.target_media_dir),
        ])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('Skipping media restoration', result.stdout)
        self.assertFalse((self.target_media_dir / 'avatar.jpg').exists())

    def test_missing_companion_media_gracefully_continues_db_only(self):
        standalone_db = self.backup_dir / 'backup_isolated_db.sql.gz'
        shutil.copy(self.valid_db_file, standalone_db)

        result = self._run_script([
            '--db-file', _to_posix_path(standalone_db),
            '--target-media-dir', _to_posix_path(self.target_media_dir),
        ])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn('No companion media archive found', result.stdout)
        self.assertIn('Restore Completed Successfully', result.stdout)

    def test_explicit_media_file_restores_correctly(self):
        custom_media = self.backup_dir / 'custom_assets.tar.gz'
        self._create_sample_media_tar(custom_media, filenames=['custom_pic.png'])

        result = self._run_script([
            '--db-file', _to_posix_path(self.valid_db_file),
            '--media-file', _to_posix_path(custom_media),
            '--target-media-dir', _to_posix_path(self.target_media_dir),
        ])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertTrue((self.target_media_dir / 'custom_pic.png').exists())

    def test_flat_media_archive_preserves_subdirectories_without_stripping(self):
        # Create an archive without 'media/' root folder, having 'avatars/pic.jpg'
        staging = Path(self.temp_dir) / 'flat_staging'
        staging.mkdir(parents=True, exist_ok=True)
        avatars = staging / 'avatars'
        avatars.mkdir(parents=True, exist_ok=True)
        (avatars / 'pic.jpg').write_bytes(b'sample_avatar')

        flat_media = self.backup_dir / 'flat_media.tar.gz'
        with tarfile.open(flat_media, 'w:gz') as tar:
            tar.add(str(avatars), arcname='avatars')

        dest_dir = Path(self.temp_dir) / 'flat_dest'
        dest_dir.mkdir(parents=True, exist_ok=True)

        result = self._run_script([
            '--db-file', _to_posix_path(self.valid_db_file),
            '--media-file', _to_posix_path(flat_media),
            '--target-media-dir', _to_posix_path(dest_dir),
        ])
        self.assertEqual(result.returncode, 0, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        # Ensure 'avatars/pic.jpg' was preserved and not stripped to 'pic.jpg'
        self.assertTrue((dest_dir / 'avatars' / 'pic.jpg').exists())

    def test_corrupt_media_archive_fails_before_any_db_drop(self):
        corrupt_media = self.backup_dir / 'corrupt_media.tar.gz'
        corrupt_media.write_bytes(b'NOT_A_VALID_TAR_GZ_FILE')

        result = self._run_script([
            '--db-file', _to_posix_path(self.valid_db_file),
            '--media-file', _to_posix_path(corrupt_media),
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn('failed gzip integrity check', result.stderr)

        if self.docker_log_file.exists():
            calls = self.docker_log_file.read_text(encoding='utf-8')
            self.assertNotIn('DROP DATABASE', calls)

    def test_psql_restore_stream_failure_fails_script_with_non_zero_exit(self):
        self._create_mock_docker(exit_code=0, psql_fail_on_restore=True)

        result = self._run_script(['--db-file', _to_posix_path(self.valid_db_file)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('syntax error in restore stream', result.stderr)
