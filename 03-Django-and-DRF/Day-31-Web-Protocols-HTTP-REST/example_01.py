"""
Day 31: Web Protocols — TCP/IP, DNS, HTTP 1.1/2/3 & REST Standards.

Covers core protocol mechanics and HTTP parsing:
1. HttpRequest & HttpResponse: Canonical protocol data models.
2. Http11Parser: Pure standard library manual parser for HTTP/1.1 wire streams
   (Request-Line, Headers, Content-Length, and Chunked Transfer-Encoding).
3. HttpConnectionPoolSimulation: Demonstrates HTTP Keep-Alive connection reuse
   vs opening new TCP connections per request.
4. Http2MultiplexingSimulator: Demonstrates binary stream multiplexing over a single
   connection, eliminating application-level Head-of-Line (HoL) blocking.
5. RestIdempotencyVerifier: Mathematical validation of Safe & Idempotent HTTP methods.
6. High-Volume Benchmark: Connection Pooling (Keep-Alive) vs Non-Keep-Alive latency.
"""

from dataclasses import dataclass, field
import io
import time
from typing import Any, Dict, List, Optional, Tuple


# =====================================================================
# 1. CANONICAL HTTP PROTOCOL DATA MODELS
# =====================================================================

@dataclass
class HttpRequest:
    """Represents a parsed HTTP request."""
    method: str
    path: str
    version: str = "HTTP/1.1"
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    query_params: Dict[str, str] = field(default_factory=dict)


@dataclass
class HttpResponse:
    """Represents an HTTP response."""
    status_code: int
    reason: str
    version: str = "HTTP/1.1"
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def serialize(self) -> bytes:
        """Serializes response into raw HTTP/1.1 wire protocol format."""
        status_line = f"{self.version} {self.status_code} {self.reason}\r\n"
        headers_block = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
        return status_line.encode("ascii") + headers_block.encode("ascii") + b"\r\n" + self.body


# =====================================================================
# 2. HTTP/1.1 WIRE PROTOCOL PARSER
# =====================================================================

class Http11Parser:
    """
    Pure Python manual HTTP/1.1 wire protocol parser.
    
    WHY MANUAL PARSING MATTERS:
    Understanding how WSGI/ASGI servers (Gunicorn, Uvicorn, Nginx) read bytes
    from operating system sockets is crucial for debugging malformed header attacks,
    HTTP request smuggling, and chunked transfer streams.
    """

    @staticmethod
    def parse_request(raw_bytes: bytes) -> HttpRequest:
        """
        Parses raw bytes from an OS socket into an HttpRequest object.
        Delimited by CRLF (\\r\\n).
        """
        stream = io.BytesIO(raw_bytes)
        
        # 1. Parse Request-Line: METHOD PATH VERSION
        request_line = stream.readline().decode("latin1").rstrip("\r\n")
        if not request_line:
            raise ValueError("Empty request stream.")

        parts = request_line.split(" ")
        if len(parts) != 3:
            raise ValueError(f"Malformed HTTP request line: {request_line}")

        method, full_path, version = parts[0], parts[1], parts[2]

        # Extract query parameters if present
        path = full_path
        query_params = {}
        if "?" in full_path:
            path, query_str = full_path.split("?", 1)
            for pair in query_str.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    query_params[k] = v

        # 2. Parse Headers until empty line (\r\n)
        headers: Dict[str, str] = {}
        while True:
            line = stream.readline().decode("latin1").rstrip("\r\n")
            if not line:
                break  # Empty line marks end of headers
            if ":" in line:
                header_name, header_val = line.split(":", 1)
                # HTTP headers are case-insensitive; normalize to lowercase
                headers[header_name.strip().lower()] = header_val.strip()

        # 3. Parse Body based on Content-Length or Chunked Encoding
        body = b""
        if "content-length" in headers:
            content_length = int(headers["content-length"])
            body = stream.read(content_length)
        elif headers.get("transfer-encoding", "").lower() == "chunked":
            # Chunked parsing: <hex_size>\r\n<data>\r\n ... 0\r\n\r\n
            while True:
                chunk_header = stream.readline().decode("latin1").rstrip("\r\n")
                if not chunk_header:
                    break
                chunk_size = int(chunk_header.split(";")[0], 16)
                if chunk_size == 0:
                    stream.readline()  # Read trailing CRLF
                    break
                chunk_data = stream.read(chunk_size)
                stream.readline()      # Read trailing CRLF after chunk
                body += chunk_data

        return HttpRequest(
            method=method.upper(),
            path=path,
            version=version,
            headers=headers,
            body=body,
            query_params=query_params,
        )


# =====================================================================
# 3. HTTP KEEP-ALIVE CONNECTION POOL SIMULATION
# =====================================================================

class HttpConnectionPoolSimulation:
    """
    Simulates the performance difference between HTTP/1.0 (new TCP handshake per request)
    and HTTP/1.1 Persistent Connections (`Connection: keep-alive`).
    """

    def __init__(self, simulated_rtt_ms: float = 10.0):
        self.rtt_ms = simulated_rtt_ms

    def request_without_keep_alive(self, count: int) -> float:
        """Each request requires 3-way TCP handshake (1 RTT) + HTTP transaction (1 RTT)."""
        # Total per request: 2 RTTs
        total_time_ms = count * (2 * self.rtt_ms)
        return total_time_ms

    def request_with_keep_alive(self, count: int) -> float:
        """First request pays TCP handshake (1 RTT). Subsequent (count - 1) requests reuse TCP stream (1 RTT each)."""
        # Initial TCP handshake (1 RTT) + count HTTP transactions (count * 1 RTT)
        total_time_ms = self.rtt_ms + (count * self.rtt_ms)
        return total_time_ms


# =====================================================================
# 4. HTTP/2 MULTIPLEXING STREAM SIMULATOR
# =====================================================================

@dataclass
class Http2Frame:
    """Simulates an HTTP/2 binary frame."""
    stream_id: int
    frame_type: str  # "HEADERS" or "DATA"
    payload: bytes
    is_end_stream: bool = False


class Http2MultiplexingSimulator:
    """
    Demonstrates HTTP/2 multiplexing over a single connection.
    
    WHY HTTP/2 MULTIPLEXING?
    HTTP/1.1 requires sequential execution or multiple TCP sockets.
    HTTP/2 interleaves independent binary frames across different stream IDs
    over a single TCP connection, eliminating application-level Head-of-Line blocking.
    """

    def __init__(self):
        self.in_flight_streams: Dict[int, List[Http2Frame]] = {}

    def send_frame(self, frame: Http2Frame) -> None:
        if frame.stream_id not in self.in_flight_streams:
            self.in_flight_streams[frame.stream_id] = []
        self.in_flight_streams[frame.stream_id].append(frame)

    def assemble_stream(self, stream_id: int) -> bytes:
        """Assembles all DATA frames for a given stream ID regardless of arrival order."""
        frames = self.in_flight_streams.get(stream_id, [])
        return b"".join(f.payload for f in frames if f.frame_type == "DATA")


# =====================================================================
# 5. REST IDEMPOTENCY VERIFIER
# =====================================================================

class RestIdempotencyVerifier:
    """
    Mathematical and operational verification of REST method contracts:
    - Safe: Read-only, leaves server state unchanged: f(x) -> state is unmodified.
    - Idempotent: Executing 1 time has the exact same side-effects as N times: f(f(x)) == f(x).
    """

    def __init__(self):
        # In-memory database of resources
        self._database: Dict[int, Dict[str, Any]] = {}

    def execute_get(self, resource_id: int) -> Tuple[Optional[Dict[str, Any]], bool]:
        """GET is Safe and Idempotent."""
        return self._database.get(resource_id), True

    def execute_put(self, resource_id: int, data: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """PUT replaces the resource entirely. It is IDEMPOTENT."""
        self._database[resource_id] = dict(data)
        return self._database[resource_id], True

    def execute_delete(self, resource_id: int) -> Tuple[bool, bool]:
        """DELETE removes resource. Calling it again leaves it deleted. It is IDEMPOTENT."""
        existed = resource_id in self._database
        if existed:
            del self._database[resource_id]
        return existed, True

    def execute_post_append(self, resource_id: int, item: str) -> Tuple[List[str], bool]:
        """POST (collection append) is NON-IDEMPOTENT. Each call appends a duplicate item!"""
        if resource_id not in self._database:
            self._database[resource_id] = {"items": []}
        self._database[resource_id]["items"].append(item)
        return self._database[resource_id]["items"], False


# =====================================================================
# 6. UNIT TESTS & VERIFICATION
# =====================================================================

def run_unit_tests():
    print("=" * 70)
    print("RUNNING UNIT TESTS FOR HTTP PROTOCOLS & REST STANDARDS")
    print("=" * 70)

    # 1. Test HTTP/1.1 Standard GET Request Parsing
    raw_get = (
        b"GET /v1/users?role=admin&active=true HTTP/1.1\r\n"
        b"Host: api.enterprise.com\r\n"
        b"Authorization: Bearer token_xyz123\r\n"
        b"Accept: application/json\r\n"
        b"\r\n"
    )
    req = Http11Parser.parse_request(raw_get)
    assert req.method == "GET"
    assert req.path == "/v1/users"
    assert req.query_params == {"role": "admin", "active": "true"}
    assert req.headers["host"] == "api.enterprise.com"
    assert req.headers["authorization"] == "Bearer token_xyz123"
    assert req.body == b""
    print("[PASS] HTTP/1.1 GET request with query params parsed successfully.")

    # 2. Test HTTP/1.1 POST Request with Content-Length
    raw_post = (
        b"POST /v1/orders HTTP/1.1\r\n"
        b"Host: api.enterprise.com\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 18\r\n"
        b"\r\n"
        b'{"item": "laptop"}'
    )
    req_post = Http11Parser.parse_request(raw_post)
    assert req_post.method == "POST"
    assert req_post.headers["content-length"] == "18"
    assert req_post.body == b'{"item": "laptop"}'
    print("[PASS] HTTP/1.1 POST with Content-Length body parsed successfully.")

    # 3. Test HTTP/1.1 Chunked Transfer-Encoding
    raw_chunked = (
        b"POST /v1/stream HTTP/1.1\r\n"
        b"Host: api.enterprise.com\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
        b"4\r\nWiki\r\n"
        b"5\r\npedia\r\n"
        b"0\r\n\r\n"
    )
    req_chunked = Http11Parser.parse_request(raw_chunked)
    assert req_chunked.body == b"Wikipedia"
    print("[PASS] HTTP/1.1 Chunked Transfer-Encoding decoded successfully.")

    # 4. Test HTTP/2 Frame Multiplexing
    h2_sim = Http2MultiplexingSimulator()
    # Stream 1: Image A; Stream 2: Image B. Interleave frames!
    h2_sim.send_frame(Http2Frame(stream_id=1, frame_type="DATA", payload=b"CHUNK_A1"))
    h2_sim.send_frame(Http2Frame(stream_id=2, frame_type="DATA", payload=b"CHUNK_B1"))
    h2_sim.send_frame(Http2Frame(stream_id=1, frame_type="DATA", payload=b"CHUNK_A2"))
    h2_sim.send_frame(Http2Frame(stream_id=2, frame_type="DATA", payload=b"CHUNK_B2"))

    assert h2_sim.assemble_stream(stream_id=1) == b"CHUNK_A1CHUNK_A2"
    assert h2_sim.assemble_stream(stream_id=2) == b"CHUNK_B1CHUNK_B2"
    print("[PASS] HTTP/2 interleaved stream multiplexing verified.")

    # 5. Test REST Idempotency Contracts
    rest = RestIdempotencyVerifier()
    # PUT: Calling 3 times produces identical state
    rest.execute_put(101, {"name": "Server A", "status": "active"})
    state1 = dict(rest._database[101])
    rest.execute_put(101, {"name": "Server A", "status": "active"})
    state2 = dict(rest._database[101])
    assert state1 == state2, "PUT violated idempotency contract!"

    # POST: Calling 2 times produces duplicate items (Non-idempotent)
    rest.execute_post_append(202, "msg_1")
    rest.execute_post_append(202, "msg_1")
    assert len(rest._database[202]["items"]) == 2  # Mutated twice!
    print("[PASS] REST idempotency contracts verified.")
    print("All unit tests passed successfully!\n")


# =====================================================================
# 7. HIGH-VOLUME BENCHMARK: KEEP-ALIVE CONNECTION POOLING
# =====================================================================

def run_performance_benchmark():
    print("=" * 70)
    print("HIGH-VOLUME BENCHMARK: HTTP KEEP-ALIVE CONNECTION POOLING")
    print("=" * 70)

    sim = HttpConnectionPoolSimulation(simulated_rtt_ms=20.0)  # 20ms cross-region latency
    num_requests = 1_000

    # Without Keep-Alive: 1 TCP Handshake (1 RTT) + 1 HTTP Exchange (1 RTT) per request
    time_no_keep_alive_ms = sim.request_without_keep_alive(num_requests)

    # With Keep-Alive: 1 TCP Handshake once, then 1 HTTP Exchange per request
    time_keep_alive_ms = sim.request_with_keep_alive(num_requests)

    saved_time_ms = time_no_keep_alive_ms - time_keep_alive_ms
    saved_seconds = saved_time_ms / 1000

    print(f"Total API Requests Evaluated:  {num_requests:,}")
    print(f"Without Keep-Alive (HTTP/1.0): {time_no_keep_alive_ms / 1000:.2f} seconds")
    print(f"With Keep-Alive (HTTP/1.1):    {time_keep_alive_ms / 1000:.2f} seconds")
    print(f"Network Latency Elimination:   {saved_seconds:.2f} seconds saved ({saved_time_ms / time_no_keep_alive_ms * 100:.1f}% reduction)")
    print("=" * 70)


if __name__ == "__main__":
    run_unit_tests()
    run_performance_benchmark()
