from __future__ import annotations

import asyncio
from tempfile import SpooledTemporaryFile

from .exceptions import BadRequest, RequestEntityTooLarge


class BodyBuffer:
    """Bounded, rewindable ingress storage. This is not a live network stream."""

    def __init__(self, maximum: int, memory_limit: int = 1024 * 1024) -> None:
        if maximum < 1 or memory_limit < 1:
            raise ValueError("body limits must be positive")
        self.maximum, self.memory_limit, self.size = maximum, memory_limit, 0
        self.file = SpooledTemporaryFile(max_size=memory_limit, mode="w+b")  # noqa: SIM115 - owned until response close

    @property
    def rolled_to_disk(self) -> bool:
        return self.size > self.memory_limit

    @property
    def closed(self) -> bool:
        return self.file.closed

    def write(self, value: bytes) -> None:
        if self.size + len(value) > self.maximum:
            raise RequestEntityTooLarge()
        self.file.write(value)
        self.size += len(value)

    def read(self, size: int = -1) -> bytes:
        return self.file.read(size)

    def fill_from(self, source, length: int | None, terminated: bool) -> None:
        remaining = length if length is not None else (self.maximum + 1 if terminated else 0)
        try:
            while remaining:
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    if length is not None:
                        raise BadRequest(detail="incomplete request body")
                    break
                if len(chunk) > remaining:
                    raise BadRequest(detail="invalid WSGI input read length")
                self.write(chunk)
                remaining -= len(chunk)
            self.seek(0)
        except BaseException:
            self.close()
            raise

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.file.seek(offset, whence)

    def tell(self) -> int:
        return self.file.tell()

    def close(self) -> None:
        self.file.close()

    def __del__(self) -> None:
        if hasattr(self, "file"):
            self.file.close()


async def store_chunk(buffer: BodyBuffer, chunk: bytes) -> None:
    if buffer.size + len(chunk) <= buffer.memory_limit:
        buffer.write(chunk)
        return
    task = asyncio.create_task(asyncio.to_thread(buffer.write, chunk))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def receive_body(storage: BodyBuffer, receive) -> bool:
    try:
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                storage.close()
                return False
            if event["type"] != "http.request":
                raise RuntimeError("expected http.request")
            await store_chunk(storage, bytes(event.get("body", b"")))
            if not event.get("more_body", False):
                storage.seek(0)
                return True
    except BaseException:
        storage.close()
        raise
