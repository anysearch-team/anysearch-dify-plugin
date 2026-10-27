import zlib
from collections.abc import Callable

import httpx

from anysearch.errors import AnySearchError

RESPONSE_LIMIT = 2 * 1024 * 1024


def read_response(response: httpx.Response, check_deadline: Callable[[], None]) -> bytes:
    content = bytearray()

    def append(chunk: bytes) -> None:
        check_deadline()
        if len(content) + len(chunk) > RESPONSE_LIMIT:
            raise AnySearchError("RESPONSE_TOO_LARGE")
        content.extend(chunk)

    if response.is_stream_consumed:
        append(response.content)
        return bytes(content)
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("identity", "gzip", "deflate", ""):
        raise AnySearchError("INVALID_RESPONSE")
    decoder = zlib.decompressobj(31 if encoding == "gzip" else 15) if encoding in ("gzip", "deflate") else None
    try:
        for raw in response.iter_raw(chunk_size=65536):
            check_deadline()
            if decoder is None:
                append(raw)
                continue
            pending = raw
            while pending:
                append(decoder.decompress(pending, min(65536, RESPONSE_LIMIT + 1 - len(content))))
                pending = decoder.unconsumed_tail
            # 不接受拼接压缩流或尾随垃圾，以免静默忽略实际响应内容。
            if decoder.unused_data:
                raise AnySearchError("INVALID_RESPONSE")
        if decoder is not None and not decoder.eof:
            raise AnySearchError("INVALID_RESPONSE")
    except zlib.error:
        raise AnySearchError("INVALID_RESPONSE") from None
    return bytes(content)
