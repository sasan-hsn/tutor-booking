from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class ResilientManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """ManifestStaticFilesStorage that gracefully handles missing entries.

    Generates content-hashed filenames during collectstatic for automated cache
    busting in production. In case an asset is referenced that is not present in
    the manifest, it falls back to the unhashed path rather than raising an
    unhandled 500 ValueError.
    """

    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except (ValueError, OSError):
            return name
