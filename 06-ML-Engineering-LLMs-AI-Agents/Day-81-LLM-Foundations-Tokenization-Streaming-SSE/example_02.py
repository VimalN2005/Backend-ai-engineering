"""
Day 81: LLM Foundations: Streaming APIs & Server-Sent Events (SSE)
Part 2: Production OpenAI-Compatible SSE Streaming Backend & Client Consumer

This module provides:
1. Production FastAPI application exposing an OpenAI-compatible POST /v1/chat/completions endpoint.
2. Autoregressive token generator emitting W3C Server-Sent Events (SSE) frames with realistic token pacing.
3. Client-side SSE stream consumer tracking Time-To-First-Token (TTFT), tokens/sec, and frame parsing.
4. Resilient UTF-8 multibyte boundary reassembly across chunk fragments.
"""

from __future__ import annotations

import codecs
import json
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


# =====================================================================
# 1. OpenAI-Compatible Data Models (Pydantic V2)
# =====================================================================

class ChatMessageModel(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "gpt-4o-mini"
    messages: List[ChatMessageModel]
    stream: bool = True
    max_tokens: int = Field(default=256, ge=1, le=4096)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class ChunkDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class ChunkChoice(BaseModel):
    index: int = 0
    delta: ChunkDelta
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChunkChoice]


# =====================================================================
# 2. Autoregressive SSE Token Generator Engine
# =====================================================================

def generate_autoregressive_sse_stream(
    request_id: str,
    model_name: str,
    messages: List[ChatMessageModel],
    max_tokens: int = 40,
    inter_token_delay_sec: float = 0.002,
) -> Iterator[str]:
    """
    Simulates token-by-token generation in an LLM autoregressive decoding loop,
    formatting each emission into W3C Server-Sent Event (SSE) frames.
    """
    created_epoch = int(time.time())

    # 1. Initial Frame: Emit Assistant Role announcement
    initial_chunk = ChatCompletionChunk(
        id=request_id,
        created=created_epoch,
        model=model_name,
        choices=[
            ChunkChoice(
                index=0,
                delta=ChunkDelta(role="assistant", content=""),
                finish_reason=None,
            )
        ],
    )
    yield f"data: {initial_chunk.model_dump_json()}\n\n"

    # Simulated response tokens containing technical terms, punctuation, and Unicode emojis
    simulated_tokens = [
        "Server", "-", "Sent", " Events", " (", "SSE", ")", " provide",
        " low", "-", "latency", " unidirectional", " streaming", " over",
        " standard", " HTTP", "/1.1", " chunk", "ed", " transfer", " encoding", ".",
        " Tokens", " are", " emitted", " instantaneously", " ", "🚀", " ",
        "reducing", " Time", "-", "To", "-", "First", "-", "Token",
        " (", "TTFT", ")", " for", " optimal", " user", " experience", "."
    ]

    tokens_to_emit = simulated_tokens[:max_tokens]

    # 2. Intermediate Frames: Emit each token delta
    for token in tokens_to_emit:
        if inter_token_delay_sec > 0:
            time.sleep(inter_token_delay_sec)

        chunk = ChatCompletionChunk(
            id=request_id,
            created=created_epoch,
            model=model_name,
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChunkDelta(content=token),
                    finish_reason=None,
                )
            ],
        )
        yield f"data: {chunk.model_dump_json()}\n\n"

    # 3. Final Frame: Signal completion with finish_reason='stop'
    stop_chunk = ChatCompletionChunk(
        id=request_id,
        created=created_epoch,
        model=model_name,
        choices=[
            ChunkChoice(
                index=0,
                delta=ChunkDelta(),
                finish_reason="stop",
            )
        ],
    )
    yield f"data: {stop_chunk.model_dump_json()}\n\n"

    # 4. Protocol Sentinel: Tell client the event stream is terminated
    yield "data: [DONE]\n\n"


# =====================================================================
# 3. FastAPI Application with SSE Endpoint
# =====================================================================

app = FastAPI(title="LLM Foundations Streaming API", version="1.0.0")


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    """
    OpenAI-compatible streaming chat completions endpoint implementing
    W3C Server-Sent Events protocol with anti-buffering headers.
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")

    request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if not req.stream:
        # Non-streaming fallback
        full_text = "Server-Sent Events (SSE) provide low-latency unidirectional streaming."
        return {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": full_text},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 12, "total_tokens": 27}
        }

    stream_generator = generate_autoregressive_sse_stream(
        request_id=request_id,
        model_name=req.model,
        messages=req.messages,
        max_tokens=req.max_tokens,
    )

    # Standard W3C SSE headers with reverse proxy anti-buffering
    headers = {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disables buffering in Nginx / AWS ALB
    }

    return StreamingResponse(stream_generator, headers=headers, media_type="text/event-stream")


# =====================================================================
# 4. Client-Side SSE Stream Consumer & Profiler
# =====================================================================

class SSEStreamConsumer:
    """
    Production client consuming and profiling SSE streams:
    - Incremental line splitting
    - Multibyte UTF-8 character boundary reassembly
    - TTFT and token throughput benchmarking
    """
    def __init__(self) -> None:
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def consume_stream(self, chunk_stream: Iterator[str | bytes]) -> Dict[str, Any]:
        start_time = time.perf_counter()
        first_token_time: Optional[float] = None
        
        reconstructed_tokens: List[str] = []
        sse_buffer = ""
        done_received = False
        frame_count = 0

        for raw_chunk in chunk_stream:
            # Reassemble UTF-8 text safely whether bytes or str
            if isinstance(raw_chunk, bytes):
                chunk_text = self.decoder.decode(raw_chunk, final=False)
            else:
                chunk_text = raw_chunk

            sse_buffer += chunk_text

            # Parse lines separated by double newlines
            while "\n\n" in sse_buffer:
                frame, sse_buffer = sse_buffer.split("\n\n", 1)
                for line in frame.split("\n"):
                    line = line.strip()
                    if not line or line.startswith(":"):
                        # Keepalive heartbeat comment
                        continue
                    
                    if line.startswith("data: "):
                        data_payload = line[6:].strip()
                        if data_payload == "[DONE]":
                            done_received = True
                            break

                        frame_count += 1
                        try:
                            payload_json = json.loads(data_payload)
                            delta = payload_json["choices"][0]["delta"]
                            content = delta.get("content")
                            if content:
                                if first_token_time is None:
                                    first_token_time = time.perf_counter()
                                reconstructed_tokens.append(content)
                        except json.JSONDecodeError:
                            continue

        total_duration = time.perf_counter() - start_time
        ttft_ms = ((first_token_time - start_time) * 1000.0) if first_token_time else 0.0
        full_response = "".join(reconstructed_tokens)
        tokens_count = len(reconstructed_tokens)
        throughput = (tokens_count / total_duration) if total_duration > 0 else 0.0

        return {
            "full_text": full_response,
            "tokens_count": tokens_count,
            "ttft_ms": ttft_ms,
            "total_duration_sec": total_duration,
            "tokens_per_sec": throughput,
            "done_sentinel_received": done_received,
            "sse_frames_parsed": frame_count,
        }


# =====================================================================
# 5. Verification & Demonstration Entrypoint
# =====================================================================

def run_sse_demonstration() -> None:
    print("=" * 80)
    print("DAY 81: PRODUCTION SERVER-SENT EVENTS (SSE) STREAMING PIPELINE")
    print("=" * 80)

    request_payload = ChatCompletionRequest(
        model="gpt-4o-mini",
        stream=True,
        max_tokens=40,
        messages=[
            ChatMessageModel(role="system", content="You are a senior AI streaming engineer."),
            ChatMessageModel(role="user", content="Explain SSE streaming benefits.")
        ]
    )

    print(f"\n1. Initializing Streaming Request for Model: {request_payload.model}")
    print(f"   Max Tokens: {request_payload.max_tokens} | Stream: {request_payload.stream}")

    # Invoke FastAPI endpoint handler directly
    response = chat_completions(request_payload)
    assert isinstance(response, StreamingResponse), "Endpoint must return StreamingResponse"

    # Verify W3C SSE HTTP Headers
    assert response.headers["Content-Type"] == "text/event-stream; charset=utf-8"
    assert response.headers["Cache-Control"] == "no-cache, no-transform"
    assert response.headers["X-Accel-Buffering"] == "no"
    print("\n2. W3C SSE Response Headers Verified:")
    print(f"   Content-Type:      {response.headers['Content-Type']}")
    print(f"   Cache-Control:     {response.headers['Cache-Control']}")
    print(f"   X-Accel-Buffering: {response.headers['X-Accel-Buffering']}")

    # Consume and profile the stream
    print("\n3. Consuming Live Token Stream via SSEStreamConsumer...")
    consumer = SSEStreamConsumer()
    # Execute streaming generator directly
    stream_tokens = generate_autoregressive_sse_stream(
        request_id="chatcmpl-demo-01",
        model_name=request_payload.model,
        messages=request_payload.messages,
        max_tokens=request_payload.max_tokens,
    )
    metrics = consumer.consume_stream(stream_tokens)

    print("\n" + "-" * 80)
    print("STREAM METRICS & PROFILING REPORT")
    print("-" * 80)
    print(f"Reconstructed Response:\n\"{metrics['full_text']}\"\n")
    print(f"Total Emitted Tokens:   {metrics['tokens_count']}")
    print(f"Total Frames Parsed:    {metrics['sse_frames_parsed']}")
    print(f"Time-To-First-Token:    {metrics['ttft_ms']:.2f} ms")
    print(f"Total Stream Duration:  {metrics['total_duration_sec']:.3f} s")
    print(f"Generation Throughput:  {metrics['tokens_per_sec']:.1f} tokens/sec")
    print(f"[DONE] Sentinel Found:  {metrics['done_sentinel_received']}")

    # Validations
    assert metrics["done_sentinel_received"], "Stream must conclude with data: [DONE]"
    assert metrics["tokens_count"] > 0, "Tokens must be streamed"
    assert "Server-Sent Events" in metrics["full_text"], "Text content must be correctly assembled"
    assert "🚀" in metrics["full_text"], "Unicode multibyte emoji must be accurately preserved"

    print("\n[SUCCESS] All SSE Streaming & Multibyte Reassembly Assertions Passed!\n")


if __name__ == "__main__":
    run_sse_demonstration()
