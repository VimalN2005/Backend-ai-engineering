# Practice Exercises: Web Protocols & REST Standards

Mastering web protocols requires implementing three fundamental wire-level and gateway mechanisms:
1. **HTTP/1.1 Chunked Streaming Protocol** — Decoding dynamic chunk streams without predefined Content-Length.
2. **Conditional Validation Caching (ETag / 304 Not Modified)** — Bandwidth-saving cache validation.
3. **CORS Subdomain Wildcard Engine** — Production-grade dynamic origin matching without security vulnerabilities.

---

## Challenge 1: HTTP/1.1 Chunked Transfer-Encoding Decoder

### Problem Statement
In streaming APIs (e.g. LLM token streaming, server-sent events, large file transfers), the total payload size is unknown at the start. HTTP/1.1 handles this using `Transfer-Encoding: chunked`.

Implement a robust `ChunkedStreamDecoder` that parses raw chunked bytes:
- Each chunk begins with its size in **hexadecimal ASCII** followed by `\r\n`.
- The chunk data follows, terminated by `\r\n`.
- The final terminating chunk has size `0\r\n\r\n`.

### Optimal Solution (Pure Standard Library Wire Decoder)

```python
import io
from typing import List

class ChunkedStreamDecoder:
    """
    Decodes HTTP/1.1 Chunked Transfer-Encoding wire streams.
    Format:
    <hex_size>[;extensions]\r\n<data>\r\n ... 0\r\n\r\n
    """

    @staticmethod
    def decode(raw_stream: bytes) -> bytes:
        stream = io.BytesIO(raw_stream)
        accumulated_payload = bytearray()

        while True:
            # Read chunk size line
            size_line = stream.readline().decode("latin1").rstrip("\r\n")
            if not size_line:
                break

            # Strip chunk extensions if any (e.g., '1a;ext=foo')
            hex_size = size_line.split(";")[0].strip()
            chunk_length = int(hex_size, 16)

            if chunk_length == 0:
                # End of chunked stream: read terminating CRLF
                stream.readline()
                break

            # Read exactly chunk_length bytes of data
            chunk_data = stream.read(chunk_length)
            accumulated_payload.extend(chunk_data)

            # Read the trailing CRLF after chunk data
            crlf = stream.read(2)
            if crlf != b"\r\n":
                raise ValueError(f"Malformed chunked stream: expected CRLF after chunk data, got {crlf}")

        return bytes(accumulated_payload)

    @staticmethod
    def encode(chunks: List[bytes]) -> bytes:
        """Encodes an array of payloads into HTTP/1.1 chunked format."""
        out = bytearray()
        for c in chunks:
            if not c:
                continue
            hex_len = f"{len(c):X}\r\n".encode("ascii")
            out.extend(hex_len)
            out.extend(c)
            out.extend(b"\r\n")
        # Terminating chunk
        out.extend(b"0\r\n\r\n")
        return bytes(out)


# Verification
if __name__ == "__main__":
    sample_chunks = [b"Hello, ", b"world of ", b"HTTP/1.1!"]
    wire_bytes = ChunkedStreamDecoder.encode(sample_chunks)
    # Wire bytes: 7\r\nHello, \r\n9\r\nworld of \r\n9\r\nHTTP/1.1!\r\n0\r\n\r\n
    decoded = ChunkedStreamDecoder.decode(wire_bytes)
    assert decoded == b"Hello, world of HTTP/1.1!"
    print("[PASS] Challenge 1: Chunked Transfer-Encoding encoder & decoder verified.")
```

---

## Challenge 2: ETag & Conditional GET (304 Not Modified) Engine

### Problem Statement
Implement an HTTP caching validator:
1. `generate_etag(payload: bytes, weak: bool = False) -> str`: Strong ETag uses SHA-256; Weak ETag prefixes `W/`.
2. `validate_conditional_get(client_if_none_match: str, current_etag: str) -> Tuple[int, bytes]`:
   - If ETags match, return `(304, b"")` (Not Modified).
   - If ETags differ, return `(200, current_payload)`.

### Optimal Solution

```python
import hashlib
from typing import Tuple

class ETagValidator:
    """
    Implements RFC 9110 ETag generation and validation semantics.
    """

    @staticmethod
    def generate_etag(payload: bytes, weak: bool = False) -> str:
        digest = hashlib.sha256(payload).hexdigest()[:16]
        if weak:
            return f'W/"{digest}"'
        return f'"{digest}"'

    @staticmethod
    def validate_request(
        client_if_none_match: str | None,
        current_payload: bytes,
        weak: bool = False,
    ) -> Tuple[int, str, bytes]:
        """
        Returns (status_code, etag, response_body).
        """
        current_etag = ETagValidator.generate_etag(current_payload, weak=weak)

        if client_if_none_match is not None:
            # Handle multiple ETags or wildcard '*'
            client_tags = [t.strip() for t in client_if_none_match.split(",")]
            if "*" in client_tags or current_etag in client_tags:
                # Cache Valid: Return 304 with ZERO body bytes
                return 304, current_etag, b""

        # Cache Stale or Not Present: Return 200 with full payload
        return 200, current_etag, current_payload


# Verification
if __name__ == "__main__":
    content_v1 = b'{"status": "ok", "version": 1}'
    etag_v1 = ETagValidator.generate_etag(content_v1)

    # First request: Client has no cache
    status, etag, body = ETagValidator.validate_request(None, content_v1)
    assert status == 200 and body == content_v1

    # Second request: Client sends If-None-Match with matching ETag
    status, etag, body = ETagValidator.validate_request(etag_v1, content_v1)
    assert status == 304 and body == b""  # Empty body!

    # Content changes on server
    content_v2 = b'{"status": "ok", "version": 2}'
    status, etag, body = ETagValidator.validate_request(etag_v1, content_v2)
    assert status == 200 and body == content_v2  # Returns new payload
    print("[PASS] Challenge 2: ETag & 304 Conditional Validation verified.")
```

---

## Challenge 3: CORS Dynamic Subdomain Origin Validator

### Problem Statement
In enterprise SaaS platforms, customers access APIs from dynamically created subdomains (e.g. `tenant-a.enterprise.com`, `tenant-b.enterprise.com`). Hardcoding static origins is impossible.
Implement a `CorsOriginMatcher` that safely matches patterns like `https://*.enterprise.com`:
- Must defend against subdomain hijacking (e.g. `https://evil-enterprise.com` must NOT match).
- Must validate protocol scheme (`http` vs `https`).

### Optimal Solution

```python
import re
from urllib.parse import urlparse

class CorsOriginMatcher:
    """
    Safely validates wildcard subdomain origins without regex injection.
    """

    def __init__(self, allowed_patterns: list[str]):
        # e.g. ["https://*.enterprise.com", "https://app.corp.io"]
        self.patterns = allowed_patterns
        self._compiled = [self._compile_pattern(p) for p in allowed_patterns]

    def _compile_pattern(self, pattern: str) -> re.Pattern:
        # Escape pattern, but replace escaped wildcard '\*' with valid subdomain regex
        escaped = re.escape(pattern)
        # Regex matches [a-zA-Z0-9_-]+ for subdomains
        regex_str = "^" + escaped.replace(r"\*", r"[a-zA-Z0-9-]+") + "$"
        return re.compile(regex_str, re.IGNORECASE)

    def is_origin_allowed(self, origin: str) -> bool:
        if not origin:
            return False
        # Normalize and validate origin syntax
        parsed = urlparse(origin)
        if not parsed.scheme or not parsed.netloc:
            return False
        
        normalized = f"{parsed.scheme}://{parsed.netloc}"
        return any(regex.match(normalized) for regex in self._compiled)


# Verification
if __name__ == "__main__":
    matcher = CorsOriginMatcher(["https://*.enterprise.com", "https://api.internal.net:8443"])

    # Valid matches
    assert matcher.is_origin_allowed("https://analytics.enterprise.com") is True
    assert matcher.is_origin_allowed("https://tenant-42.enterprise.com") is True
    assert matcher.is_origin_allowed("https://api.internal.net:8443") is True

    # Security attacks: Must be rejected!
    assert matcher.is_origin_allowed("http://analytics.enterprise.com") is False  # Insecure http
    assert matcher.is_origin_allowed("https://evil-enterprise.com") is False      # Suffix attack
    assert matcher.is_origin_allowed("https://enterprise.com.evil.com") is False  # Domain spoof
    print("[PASS] Challenge 3: Secure CORS Wildcard Subdomain Matcher verified.")
```
