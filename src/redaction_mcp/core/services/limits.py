"""Shared upload and direct-processing limits."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TypeVar

_Result = TypeVar("_Result")

CHUNK_SIZE = 1024 * 1024
DIRECT_LIMIT = 16 * 1024 * 1024
_DIRECT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="redaction-direct")
_IO_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="redaction-io")


async def _run_in_pool(pool: ThreadPoolExecutor, task: Callable[..., _Result], *args: object) -> _Result:
    future = pool.submit(partial(task, *args))
    # The short timer also wakes event loops whose thread-safe callback signal
    # is unavailable in a restricted host.
    while not future.done():
        await asyncio.sleep(0.01)
    return future.result()


async def run_direct(task: Callable[..., _Result], *args: object) -> _Result:
    """Bound concurrent in-memory work across API transports."""
    return await _run_in_pool(_DIRECT_EXECUTOR, task, *args)


async def run_io(task: Callable[..., _Result], *args: object) -> _Result:
    """Keep durable-job disk and SQLite work off the event loop."""
    return await _run_in_pool(_IO_EXECUTOR, task, *args)
LARGE_LIMIT = 512 * 1024 * 1024
OFFICE_LIMIT = 32 * 1024 * 1024
ARCHIVE_EXPANDED_LIMIT = 256 * 1024 * 1024
OFFICE_EXTENSIONS = {".docx", ".pptx", ".pptm", ".xlsx"}
