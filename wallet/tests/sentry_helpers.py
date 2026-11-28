import sentry_sdk


def install_sentry_capture(test_case) -> list[dict]:
    """
    Configure Sentry SDK with an in-memory transport for the duration of a test
    and restore the previous client on cleanup.
    """
    captured: list[dict] = []
    prev_client = sentry_sdk.get_global_scope().get_client()
    sentry_sdk.init(
        dsn="https://public@example.ingest.sentry.io/1",
        transport=captured.append,
        default_integrations=False,
    )
    test_case.addCleanup(sentry_sdk.get_global_scope().set_client, prev_client)
    return captured
