"""
Concurrency testing utilities for multi-threaded database testing.
Ensures independent database connections per thread with proper synchronization and cleanup.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, List, Optional, Tuple

from django.db import connection


def run_concurrently(
    targets: List[Callable[[], Any]],
    timeout: Optional[float] = 10.0,
) -> List[Tuple[Optional[Any], Optional[Exception]]]:
    """
    Executes a list of zero-argument callables concurrently using ThreadPoolExecutor.

    Synchronizes thread start via threading.Barrier to ensure maximum contention,
    and guarantees each thread's Django database connection is closed cleanly in a finally block.

    Returns a list of (result, exception) tuples in the same order as targets.
    """
    num_threads = len(targets)
    if num_threads == 0:
        return []

    barrier = threading.Barrier(num_threads)
    results: List[Tuple[Optional[Any], Optional[Exception]]] = [
        (None, None) for _ in range(num_threads)
    ]

    def _worker(index: int, target: Callable[[], Any]):
        try:
            # Synchronize all threads at the barrier before executing the workload
            barrier.wait(timeout=timeout)
            res = target()
            results[index] = (res, None)
        except Exception as exc:
            results[index] = (None, exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [
            executor.submit(_worker, i, target) for i, target in enumerate(targets)
        ]
        for future in as_completed(futures):
            future.result()

    return results
