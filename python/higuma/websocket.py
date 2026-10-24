from __future__ import annotations

import asyncio
import json
from typing import Any


class WebSocketDisconnect(ConnectionError):
    def __init__(self, code: int = 1000, reason: str = "") -> None:
        super().__init__(f"WebSocket disconnected ({code}): {reason}")
        self.code = code
        self.reason = reason


class WebSocket:
    def __init__(self, session: Any) -> None:
        self._session = session

    def send(self, data: str | bytes) -> None:
        if isinstance(data, str):
            self.send_text(data)
        else:
            self.send_bytes(data)

    def send_text(self, data: str) -> None:
        self._session.send_text(data)

    def send_bytes(self, data: bytes | bytearray | memoryview) -> None:
        self._session.send_bytes(bytes(data))

    def send_json(self, data: Any) -> None:
        self.send_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    def receive(self) -> str | bytes:
        kind, text, data, code, reason = self._session.receive()
        if kind == "text":
            return text
        if kind == "bytes":
            return bytes(data)
        raise WebSocketDisconnect(int(code or 1000), str(reason or ""))

    def receive_text(self) -> str:
        value = self.receive()
        if not isinstance(value, str):
            raise TypeError("expected a text WebSocket message")
        return value

    def receive_bytes(self) -> bytes:
        value = self.receive()
        if not isinstance(value, bytes):
            raise TypeError("expected a binary WebSocket message")
        return value

    def receive_json(self) -> Any:
        return json.loads(self.receive_text())

    def close(self, code: int = 1000, reason: str = "") -> None:
        self._session.close(code, reason)

    async def asend_text(self, data: str) -> None:
        if hasattr(self._session, "asend_text"):
            await self._session.asend_text(data)
        else:
            await asyncio.to_thread(self.send_text, data)

    async def asend_bytes(self, data: bytes | bytearray | memoryview) -> None:
        if hasattr(self._session, "asend_bytes"):
            await self._session.asend_bytes(bytes(data))
        else:
            await asyncio.to_thread(self.send_bytes, data)

    async def asend_json(self, data: Any) -> None:
        await self.asend_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    async def areceive(self) -> str | bytes:
        if not hasattr(self._session, "areceive"):
            return await asyncio.to_thread(self.receive)
        kind, text, data, code, reason = await self._session.areceive()
        if kind == "text":
            return text
        if kind == "bytes":
            return bytes(data)
        raise WebSocketDisconnect(int(code or 1000), str(reason or ""))

    async def areceive_text(self) -> str:
        value = await self.areceive()
        if not isinstance(value, str):
            raise TypeError("expected a text WebSocket message")
        return value

    async def areceive_bytes(self) -> bytes:
        value = await self.areceive()
        if not isinstance(value, bytes):
            raise TypeError("expected a binary WebSocket message")
        return value

    async def areceive_json(self) -> Any:
        return json.loads(await self.areceive_text())

    async def aclose(self, code: int = 1000, reason: str = "") -> None:
        if hasattr(self._session, "aclose"):
            await self._session.aclose(code, reason)
        else:
            await asyncio.to_thread(self.close, code, reason)
