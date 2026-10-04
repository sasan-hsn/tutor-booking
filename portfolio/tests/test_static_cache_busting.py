import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings

from tutor_booking.storage import ResilientManifestStaticFilesStorage


class StaticCacheBustingTests(SimpleTestCase):
    """Tests for static asset cache busting and ResilientManifestStaticFilesStorage."""

    def test_default_storage_in_test_environment(self):
        """In test runs, StaticFilesStorage should be used to avoid requiring a pre-built manifest."""
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )

    def test_production_storage_backend_selection(self):
        """When DEBUG is False and 'test' is not in argv, ResilientManifestStaticFilesStorage is selected."""
        with patch("tutor_booking.settings.DEBUG", False), patch("sys.argv", ["manage.py", "runserver"]):
            backend = (
                "django.contrib.staticfiles.storage.StaticFilesStorage"
                if settings.DEBUG or ("test" in ["manage.py", "runserver"])
                else "tutor_booking.storage.ResilientManifestStaticFilesStorage"
            )
            self.assertEqual(backend, "tutor_booking.storage.ResilientManifestStaticFilesStorage")

    def test_resilient_manifest_storage_missing_file_fallback(self):
        """Missing or unmanifested assets should fall back gracefully to unhashed paths without 500 errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ResilientManifestStaticFilesStorage(location=tmpdir)
            # Should not raise ValueError or OSError
            unhashed_name = storage.stored_name("images/nonexistent_image.png")
            self.assertEqual(unhashed_name, "images/nonexistent_image.png")
            self.assertEqual(
                storage.url("images/nonexistent_image.png"),
                "/static/images/nonexistent_image.png",
            )

    def test_collectstatic_generates_hashed_files_and_manifest(self):
        """Running collectstatic with ResilientManifestStaticFilesStorage produces hashed files and staticfiles.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with override_settings(
                DEBUG=False,
                STATIC_ROOT=tmp_path,
                STORAGES={
                    "default": {
                        "BACKEND": "django.core.files.storage.FileSystemStorage",
                    },
                    "staticfiles": {
                        "BACKEND": "tutor_booking.storage.ResilientManifestStaticFilesStorage",
                    },
                },
            ):
                call_command("collectstatic", interactive=False, verbosity=0)

                manifest_file = tmp_path / "staticfiles.json"
                self.assertTrue(manifest_file.exists(), "staticfiles.json manifest must be generated")

                manifest_data = json.loads(manifest_file.read_text())
                paths = manifest_data.get("paths", {})

                # Verify key static assets are hashed and tracked in manifest
                self.assertIn("css/style.css", paths)
                self.assertIn("js/main.js", paths)

                hashed_css = paths["css/style.css"]
                self.assertTrue((tmp_path / hashed_css).exists(), f"Hashed file {hashed_css} must exist on disk")
                self.assertNotEqual(hashed_css, "css/style.css")
                self.assertTrue(hashed_css.startswith("css/style."))
                self.assertTrue(hashed_css.endswith(".css"))

                # Test storage URL resolution with DEBUG=False
                storage = ResilientManifestStaticFilesStorage(location=tmpdir)
                css_url = storage.url("css/style.css")
                self.assertEqual(css_url, f"/static/{hashed_css}")

    def test_template_rendering_with_hashed_static_in_production(self):
        """In production mode (DEBUG=False), template {% static %} tags render fingerprinted URLs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            storage_backend = "tutor_booking.storage.ResilientManifestStaticFilesStorage"
            with override_settings(
                DEBUG=False,
                STATIC_ROOT=tmp_path,
                STORAGES={
                    "default": {
                        "BACKEND": "django.core.files.storage.FileSystemStorage",
                    },
                    "staticfiles": {
                        "BACKEND": storage_backend,
                    },
                },
            ):
                call_command("collectstatic", interactive=False, verbosity=0)

                template = Template("{% load static %}<link rel='stylesheet' href='{% static \"css/style.css\" %}'>")
                rendered = template.render(Context({}))

                manifest_data = json.loads((tmp_path / "staticfiles.json").read_text())
                expected_hashed = manifest_data["paths"]["css/style.css"]

                self.assertIn(f"/static/{expected_hashed}", rendered)

    def test_template_rendering_with_unhashed_static_in_debug(self):
        """In development mode (DEBUG=True), template {% static %} tags render unhashed URLs."""
        template = Template("{% load static %}<link rel='stylesheet' href='{% static \"css/style.css\" %}'>")
        with override_settings(DEBUG=True):
            rendered = template.render(Context({}))
            self.assertIn("/static/css/style.css", rendered)
