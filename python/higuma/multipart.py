from __future__ import annotations

import re
from email import policy
from email.parser import BytesHeaderParser
from io import BytesIO
from tempfile import SpooledTemporaryFile
from typing import Any

from .exceptions import BadRequest, RequestEntityTooLarge


class _Reader:
    def __init__(self, source: Any) -> None:
        self.source = source
        self.buffer = b""
        self.eof = False

    def fill(self) -> bool:
        chunk = self.source.read(65536)
        self.eof = not chunk
        self.buffer += chunk
        return bool(chunk)

    def until(
        self, marker: bytes, sink: Any, *, maximum: int | None = None, delimiter=False
    ) -> None:
        written = 0
        while True:
            index = self.buffer.find(marker)
            while index >= 0:
                end = index + len(marker)
                while delimiter and len(self.buffer) < end + 2:
                    if not self.fill():
                        raise BadRequest(detail="incomplete multipart delimiter")
                valid = not delimiter or self.buffer[end : end + 2] == b"\r\n"
                if delimiter and self.buffer[end : end + 2] == b"--":
                    while len(self.buffer) < end + 4 and not self.eof:
                        self.fill()
                    valid = self.buffer[end + 2 : end + 4] == b"\r\n" or (
                        self.eof and len(self.buffer) == end + 2
                    )
                if valid:
                    value, self.buffer = self.buffer[:index], self.buffer[end:]
                    if maximum is not None and written + len(value) > maximum:
                        raise RequestEntityTooLarge(detail="multipart field/header is too large")
                    sink.write(value)
                    return
                index = self.buffer.find(marker, index + 1)
            keep = len(marker) + 4
            if len(self.buffer) > keep:
                value, self.buffer = self.buffer[:-keep], self.buffer[-keep:]
                written += len(value)
                if maximum is not None and written > maximum:
                    raise RequestEntityTooLarge(detail="multipart field/header is too large")
                sink.write(value)
            if not self.fill():
                raise BadRequest(detail="incomplete multipart body")

    def take(self, count: int) -> bytes:
        while len(self.buffer) < count:
            if not self.fill():
                raise BadRequest(detail="incomplete multipart body")
        value, self.buffer = self.buffer[:count], self.buffer[count:]
        return value


def parse_multipart(request: Any) -> tuple[list[Any], list[Any]]:
    from .request import UploadFile

    content_type = request.content_type or ""
    if any(char in content_type for char in "\r\n\x00"):
        raise BadRequest(detail="invalid multipart Content-Type")
    parser = BytesHeaderParser(policy=policy.default)
    header = parser.parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n")
    boundary = header.get_boundary()
    if (
        boundary is None
        or not re.fullmatch(r"[A-Za-z0-9'()+_,./:=? -]{1,70}", boundary)
        or boundary.endswith(" ")
    ):
        raise BadRequest(detail="multipart request is missing a valid boundary")
    marker = b"--" + boundary.encode("ascii")
    fields, files, opened = [], [], []
    total_fields = 0
    source = request.stream
    source.seek(0)
    reader = _Reader(source)
    try:
        # Preamble is supported, but cannot grow without a bound.
        reader.until(marker, BytesIO(), maximum=65536, delimiter=True)
        for _ in range(request.max_form_parts):
            suffix = reader.take(2)
            if suffix == b"--":
                return fields, files
            if suffix != b"\r\n":
                raise BadRequest(detail="invalid multipart delimiter")
            header_bytes = BytesIO()
            reader.until(b"\r\n\r\n", header_bytes, maximum=65536)
            part = parser.parsebytes(header_bytes.getvalue() + b"\r\n\r\n")
            if part.defects or len(part.get_all("Content-Disposition", [])) != 1:
                raise BadRequest(detail="invalid multipart part headers")
            if part.get_content_disposition() != "form-data":
                raise BadRequest(detail="multipart parts must use form-data")
            if part.get("Content-Transfer-Encoding", "binary").lower() not in {"binary", "8bit"}:
                raise BadRequest(detail="encoded multipart transfer is not supported")
            name = part.get_param("name", header="content-disposition")
            if not name:
                raise BadRequest(detail="multipart part is missing its name")
            filename = part.get_filename()
            if filename is None:
                payload = BytesIO()
                reader.until(
                    b"\r\n" + marker,
                    payload,
                    maximum=request.max_form_memory_size - total_fields,
                    delimiter=True,
                )
                raw = payload.getvalue()
                total_fields += len(raw)
                try:
                    value = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
                except LookupError as error:
                    raise BadRequest(detail="unknown multipart charset") from error
                fields.append((name, value))
            else:
                file = SpooledTemporaryFile(max_size=request.upload_memory_limit, mode="w+b")  # noqa: SIM115 - transferred to UploadFile
                opened.append(file)
                reader.until(b"\r\n" + marker, file, delimiter=True)
                size = file.tell()
                file.seek(0)
                files.append(
                    (
                        name,
                        UploadFile(
                            filename,
                            stream=file,
                            size=size,
                            content_type=part.get_content_type(),
                            headers=dict(part.items()),
                            field_name=name,
                        ),
                    )
                )
        # A closing delimiter does not count as another part.
        if reader.take(2) == b"--":
            return fields, files
        raise RequestEntityTooLarge(detail="too many multipart parts")
    except BaseException:
        for file in opened:
            file.close()
        raise
