import asyncio, json, sys, time, uuid, argparse, logging
from datetime import datetime, timezone

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from .utils import (
    BACKENDS, STRIP_PARAMS_BY_BACKEND,
    ALL_TARGETS, TARGET_CHOICES,
    load_or_prompt_keys, manage_keys,
    _run_preflight_checks, _detect_config_conflicts,
    _safe_write_json, setup_logging,
    _detect_installed_targets,
    increment_requests, decrement_requests, log_request_status,
)
from .models import (
    MODEL_MAP, CLAUDE_MODEL_MAP, ACTIVE_MODELS,
    get_all_models, interactive_model_selection, test_model_live,
    _resolve_model,
)
from .translate import (
    _post_with_retry, _async_post_stream,
    _anthropic_to_openai, _normalize_openai_messages,
    _openai_to_anthropic,
    _anthropic_response_to_openai,
    _anthropic_event_to_openai_chunks,
    _chunk_text,
    _openai_chunk_to_anthropic_events,
    _openai_response_to_anthropic,
    _gemini_to_openai,
    _openai_to_gemini_response,
    _openai_chunk_to_gemini_chunk,
    _responses_to_openai,
    _openai_to_responses_response,
    _openai_chunk_to_responses_events,
)
from ..configs import opencode, claude_code, codex, cursor, antigravity

logger = logging.getLogger("g4f-bridge")

PORT = 1337

# Auto-continuation for upstream streams that drop mid-response.
MAX_STREAM_CONTINUATIONS = 3
STREAM_CONTINUATION_NUDGE = (
    "Continue your previous response exactly where it left off. "
    "Do not repeat any text already written."
)

# Seconds of upstream silence before sending an SSE keepalive ping to the
# client (OpenCode et al.) so the connection doesn't appear dead while the
# upstream model is still thinking (high time-to-first-token models like GLM).
STREAM_KEEPALIVE_INTERVAL = 5.0

# Sentinel pushed by the upstream reader pump when the httpx stream ends.
_STREAM_END = object()

def _upstream_error_message(err_text):
    if isinstance(err_text, bytes):
        err_text = err_text.decode(errors="replace")
    try:
        data = json.loads(err_text)
    except (json.JSONDecodeError, TypeError):
        return err_text
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            return err.get("message", json.dumps(err))
        return str(err)
    return err_text

def _req_id(request):
    return getattr(request.state, 'req_id', '--------')

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def track_requests(request: Request, call_next):
    req_id = uuid.uuid4().hex[:8]
    request.state.req_id = req_id
    increment_requests()
    log_request_status()
    start = time.time()
    response = await call_next(request)

    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None:
        # Non-streaming response — body already fully rendered.
        elapsed = time.time() - start
        decrement_requests()
        logger.info(f"[{req_id}] {request.method} {request.url.path} completed in {elapsed:.2f}s")
        log_request_status()
        return response

    async def counting_body():
        try:
            async for chunk in body_iterator:
                yield chunk
        finally:
            elapsed = time.time() - start
            decrement_requests()
            logger.info(f"[{req_id}] {request.method} {request.url.path} completed in {elapsed:.2f}s")
            log_request_status()

    response.body_iterator = counting_body()
    return response

@app.get("/v1/models")
async def list_models(request: Request):
    is_anthropic = "anthropic-version" in request.headers or "anthropic" in request.headers.get("user-agent", "").lower()
    now_iso = datetime.now(timezone.utc).isoformat()
    now_ts = int(time.time())
    models = []
    claude_entries = []
    seen_claude_names = set()
    for label, m in MODEL_MAP.items():
        if ACTIVE_MODELS and label not in ACTIVE_MODELS:
            continue
        display = label.split(":")[-1].split("/")[-1] if ":" in label or "/" in label else label
        provider_tag = label.split(":")[0] if ":" in label else m.get("backend", "?")
        display_name = f"{provider_tag} {display}"
        models.append({
            "id": label,
            "display_name": display_name,
            "object": "model",
            "created": now_ts,
            "owned_by": m.get("backend", "bridge"),
            "type": "model",
            "created_at": now_iso,
            "capabilities": {"image_input": {"supported": False}},
        })
        claude_name = f"claude-{display}"
        if claude_name not in seen_claude_names and claude_name != label:
            seen_claude_names.add(claude_name)
            claude_entries.append({
                "id": claude_name,
                "display_name": display_name,
                "object": "model",
                "created": now_ts,
                "owned_by": m.get("backend", "bridge"),
                "type": "model",
                "created_at": now_iso,
                "capabilities": {"image_input": {"supported": False}},
            })
    all_entries = models + claude_entries
    return {"object": "list", "data": all_entries}

@app.post("/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request):
    try:
        payload = await request.json()
        messages = payload.get("messages", [])
        system = payload.get("system", "")
        total_chars = len(str(system))
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(str(block.get("text", "")))
                        total_chars += len(str(block.get("content", "")))
        estimated_tokens = max(1, total_chars // 4)
        return {"input_tokens": estimated_tokens}
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "type": "error",
            "error": {"type": "api_error", "message": str(e)}
        })

@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "Invalid JSON body"}
        })

    requested_label = payload.get("model", "")
    logger.info(f"[{_req_id(request)}] [Anthropic] Incoming request for model: '{requested_label}'")

    if "thinking" in payload:
        logger.debug("Stripping 'thinking' field")
        del payload["thinking"]
    if "context_management" in payload:
        logger.debug("Stripping 'context_management' field")
        del payload["context_management"]
    if "output_config" in payload:
        logger.debug("Stripping 'output_config' field")
        del payload["output_config"]

    model_obj = _resolve_model(requested_label)
    if model_obj is None:
        logger.warning(f"Rejected: Model '{requested_label}' is not recognized.")
        return JSONResponse(status_code=400, content={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": f"Model '{requested_label}' not recognized."}
        })
    backend = model_obj["backend"]
    actual_model_id = model_obj["id"]
    backend_url = BACKENDS[backend]["url"]
    backend_key = BACKENDS[backend]["key"]

    if backend == "AGENTROUTER" and _is_anthropic_model(requested_label):
        payload["model"] = actual_model_id
        return await _agentrouter_anthropic_messages(request, payload, requested_label)

    openai_payload = _anthropic_to_openai(payload)
    openai_payload["model"] = actual_model_id

    for key in STRIP_PARAMS_BY_BACKEND.get(backend, []):
        if key in openai_payload:
            del openai_payload[key]

    logger.info(f"[{_req_id(request)}] [Anthropic] Proxying {requested_label} to {backend}")

    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }

    is_stream = payload.get("stream", False)
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    try:
        if is_stream:
            openai_payload["stream"] = True
            try:
                resp = await _async_post_stream(
                    f"{backend_url}/chat/completions",
                    openai_payload,
                    headers,
                )
            except httpx.RequestError as e:
                logger.error(f"[Anthropic] Upstream connection failed: {e}")
                return JSONResponse(status_code=502, content={
                    "type": "error",
                    "error": {"type": "api_error", "message": str(e)}
                })

            if resp.status_code != 200:
                err_text = await resp.aread()
                await resp.aclose()
                logger.error(f"[Anthropic] Upstream error ({resp.status_code}): {err_text}")
                return JSONResponse(status_code=resp.status_code, content={
                    "type": "error",
                    "error": {"type": "api_error", "message": _upstream_error_message(err_text)}
                })

            async def anthropic_stream_generator():
                logger.info("[Anthropic] Streaming response back")
                is_first = True
                try:
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            return
                        if not line:
                            continue
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(data_str)
                            events, is_first = _openai_chunk_to_anthropic_events(
                                chunk_json, msg_id, requested_label, is_first
                            )
                            for event in events:
                                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode('utf-8')
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue
                    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode('utf-8')
                finally:
                    await resp.aclose()

            return StreamingResponse(anthropic_stream_generator(), media_type="text/event-stream")
        else:
            openai_payload["stream"] = False
            response = _post_with_retry(f"{backend_url}/chat/completions", openai_payload, headers)
            if response.status_code != 200:
                return JSONResponse(status_code=response.status_code, content={
                    "type": "error",
                    "error": {"type": "api_error", "message": response.text}
                })
            anthropic_resp = _openai_response_to_anthropic(response.json(), requested_label)
            return JSONResponse(content=anthropic_resp)
    except Exception as e:
        logger.exception(f"[Anthropic] Critical error")
        return JSONResponse(status_code=500, content={
            "type": "error",
            "error": {"type": "api_error", "message": str(e)}
        })

@app.get("/v1beta/models")
async def gemini_list_models():
    models = []
    for label, m in MODEL_MAP.items():
        models.append({
            "name": f"models/{label}",
            "displayName": label,
            "description": f"{m.get('backend', 'bridge')} model proxied via bridge",
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]
        })
    return {"models": models}

@app.api_route("/v1beta/models/{model_path:path}", methods=["GET", "POST"])
async def gemini_router(request: Request, model_path: str):
    if request.method == "GET":
        return await gemini_list_models()

    if ":generateContent" in model_path:
        return await gemini_generate_content(request, model_path, stream=False)
    elif ":streamGenerateContent" in model_path:
        return await gemini_generate_content(request, model_path, stream=True)
    else:
        return JSONResponse(status_code=400, content={
            "error": {"code": 400, "message": "Unknown action in path", "status": "INVALID_ARGUMENT"}
        })

async def gemini_generate_content(request: Request, model_path: str, stream=False):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "error": {"code": 400, "message": "Invalid JSON body", "status": "INVALID_ARGUMENT"}
        })

    model_label = model_path.split("/models/")[-1] if "/models/" in model_path else model_path
    model_label = model_label.split(":")[0]

    req_id = _req_id(request)
    logger.info(f"[{req_id}] [Gemini] Incoming request for model: '{model_label}'")

    model_obj = _resolve_model(model_label)
    if model_obj is None:
        logger.info(f"[{_req_id(request)}] [Gemini] Model '{model_label}' not found. Attempting fallback routing...")
        fallback_obj = None
        lower_label = model_label.lower()
        if not MODEL_MAP:
            return JSONResponse(status_code=500, content={"error": {"code": 500, "message": "No models available", "status": "INTERNAL"}})
        for k, v in MODEL_MAP.items():
            k_lower = k.lower()
            if ("claude" in lower_label and "claude" in k_lower) or \
               ("gemini" in lower_label and "gemini" in k_lower) or \
               ("gpt" in lower_label and "gpt" in k_lower):
                fallback_obj = v
                break
        if not fallback_obj:
            fallback_obj = next(iter(MODEL_MAP.values()))
        logger.info(f"  -> Routed '{model_label}' to fallback: '{fallback_obj['id']}'")
        model_obj = fallback_obj

    backend = model_obj["backend"]
    actual_model_id = model_obj["id"]
    backend_url = BACKENDS[backend]["url"]
    backend_key = BACKENDS[backend]["key"]

    openai_payload = _gemini_to_openai(payload, actual_model_id)

    for key in STRIP_PARAMS_BY_BACKEND.get(backend, []):
        if key in openai_payload:
            del openai_payload[key]

    logger.info(f"[{_req_id(request)}] [Gemini] Proxying {model_label} to {backend}")

    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }

    try:
        if stream:
            openai_payload["stream"] = True
            try:
                resp = await _async_post_stream(
                    f"{backend_url}/chat/completions",
                    openai_payload,
                    headers,
                )
            except httpx.RequestError as e:
                logger.error(f"[Gemini] Upstream connection failed: {e}")
                return JSONResponse(status_code=502, content={
                    "error": {"code": 502, "message": str(e), "status": "UPSTREAM_ERROR"}
                })

            if resp.status_code != 200:
                err_text = await resp.aread()
                await resp.aclose()
                logger.error(f"[Gemini] Upstream error ({resp.status_code}): {err_text}")
                return JSONResponse(status_code=resp.status_code, content={
                    "error": {"code": resp.status_code, "message": _upstream_error_message(err_text), "status": "UPSTREAM_ERROR"}
                })

            async def gemini_stream_generator():
                logger.info("[Gemini] Streaming response back")
                msg_id = f"msg_{uuid.uuid4().hex[:24]}"
                try:
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            return
                        if not line:
                            continue
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(data_str)
                            gemini_chunk = _openai_chunk_to_gemini_chunk(chunk_json, msg_id)
                            if gemini_chunk:
                                yield f"data: {json.dumps(gemini_chunk)}\n\n".encode('utf-8')
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue
                    yield b"data: [DONE]\n\n"
                finally:
                    await resp.aclose()

            return StreamingResponse(gemini_stream_generator(), media_type="text/event-stream")
        else:
            openai_payload["stream"] = False
            response = _post_with_retry(f"{backend_url}/chat/completions", openai_payload, headers)
            if response.status_code != 200:
                logger.error(f"[Gemini] Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={
                    "error": {"code": response.status_code, "message": response.text, "status": "UPSTREAM_ERROR"}
                })
            gemini_resp = _openai_to_gemini_response(response.json(), model_label)
            return JSONResponse(content=gemini_resp)
    except Exception as e:
        logger.exception(f"[Gemini] Critical error")
        return JSONResponse(status_code=500, content={
            "error": {"code": 500, "message": str(e), "status": "INTERNAL_ERROR"}
        })

@app.post("/v1/responses")
async def responses_create(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "error": {"message": "Invalid JSON body", "type": "invalid_request_error"}
        })

    requested_label = payload.get("model", "")
    logger.info(f"[{_req_id(request)}] [Responses] Incoming request for model: '{requested_label}'")

    model_obj = _resolve_model(requested_label)
    if model_obj is None:
        logger.warning(f"[Responses] Rejected: Model '{requested_label}' not recognized.")
        return JSONResponse(status_code=400, content={
            "error": {"message": f"Model '{requested_label}' not recognized.", "type": "invalid_request_error"}
        })
    backend = model_obj["backend"]
    actual_model_id = model_obj["id"]
    backend_url = BACKENDS[backend]["url"]
    backend_key = BACKENDS[backend]["key"]

    openai_payload = _responses_to_openai(payload)
    openai_payload["model"] = actual_model_id

    for key in STRIP_PARAMS_BY_BACKEND.get(backend, []):
        if key in openai_payload:
            del openai_payload[key]

    logger.info(f"[{_req_id(request)}] [Responses] Proxying {requested_label} to {backend}")

    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }

    is_stream = payload.get("stream", False)
    resp_id = f"resp_{uuid.uuid4().hex[:24]}"

    try:
        if is_stream:
            openai_payload["stream"] = True
            try:
                resp = await _async_post_stream(
                    f"{backend_url}/chat/completions",
                    openai_payload,
                    headers,
                )
            except httpx.RequestError as e:
                logger.error(f"[Responses] Upstream connection failed: {e}")
                return JSONResponse(status_code=502, content={
                    "error": {"message": str(e), "type": "api_error"}
                })

            if resp.status_code != 200:
                err_text = await resp.aread()
                await resp.aclose()
                logger.error(f"[Responses] Upstream error ({resp.status_code}): {err_text}")
                return JSONResponse(status_code=resp.status_code, content={
                    "error": {"message": _upstream_error_message(err_text), "type": "api_error"}
                })

            async def responses_stream_generator():
                logger.info("[Responses] Streaming response back")
                state = {"started": False, "item_added": False}
                try:
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            return
                        if not line:
                            continue
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(data_str)
                            events = _openai_chunk_to_responses_events(chunk_json, resp_id, state)
                            for event in events:
                                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode('utf-8')
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue
                    yield b"data: [DONE]\n\n"
                finally:
                    await resp.aclose()

            return StreamingResponse(responses_stream_generator(), media_type="text/event-stream")
        else:
            openai_payload["stream"] = False
            response = _post_with_retry(f"{backend_url}/chat/completions", openai_payload, headers)
            if response.status_code != 200:
                logger.error(f"[Responses] Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={
                    "error": {"message": response.text, "type": "api_error"}
                })
            responses_resp = _openai_to_responses_response(response.json())
            return JSONResponse(content=responses_resp)
    except Exception as e:
        logger.exception(f"[Responses] Critical error")
        return JSONResponse(status_code=500, content={
            "error": {"message": str(e), "type": "api_error"}
        })

def _is_anthropic_model(label):
    lower = label.lower()
    return "claude" in lower or "opus" in lower

def _agentrouter_anthropic_base_url():
    return BACKENDS["AGENTROUTER"]["url"].rstrip("/").removesuffix("/v1") + "/v1/messages"

async def _agentrouter_anthropic_chat(request, payload, requested_label):
    """Proxy an OpenAI chat/completions request to agentrouter's Anthropic endpoint."""
    anthropic_payload = _openai_to_anthropic(payload)
    target_url = _agentrouter_anthropic_base_url()
    headers = {
        "Authorization": f"Bearer {BACKENDS['AGENTROUTER']['key']}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    logger.info(f"[{_req_id(request)}] [Chat->Anthropic] Proxying {requested_label} to {target_url}")

    is_stream = payload.get("stream", False)
    stream_id = f"chatcmpl-bridge-{int(time.time())}"
    try:
        if is_stream:
            anthropic_payload["stream"] = True
            try:
                resp = await _async_post_stream(target_url, anthropic_payload, headers)
            except httpx.RequestError as e:
                logger.error(f"[Chat->Anthropic] Upstream connection failed: {e}")
                return JSONResponse(status_code=502, content={"error": str(e)})

            if resp.status_code != 200:
                err_text = await resp.aread()
                await resp.aclose()
                logger.error(f"[Chat->Anthropic] Upstream error ({resp.status_code}): {err_text}")
                return JSONResponse(status_code=resp.status_code, content={
                    "error": _upstream_error_message(err_text)
                })

            async def agentrouter_anthropic_stream_generator():
                state = {"tool_index": 0}
                current_data = []
                try:
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            return
                        if not line:
                            if current_data:
                                data_str = "".join(current_data)
                                try:
                                    event = json.loads(data_str)
                                    chunks = _anthropic_event_to_openai_chunks(
                                        event, stream_id, requested_label, state
                                    )
                                    for chunk in chunks:
                                        yield f"data: {json.dumps(chunk)}\n\n".encode('utf-8')
                                except (json.JSONDecodeError, IndexError, KeyError):
                                    pass
                            current_data = []
                            continue
                        if line.startswith("data: "):
                            current_data.append(line[6:])
                    yield b"data: [DONE]\n\n"
                finally:
                    await resp.aclose()

            return StreamingResponse(agentrouter_anthropic_stream_generator(), media_type="text/event-stream")
        else:
            anthropic_payload["stream"] = False
            response = _post_with_retry(target_url, anthropic_payload, headers)
            if response.status_code != 200:
                logger.error(f"[Chat->Anthropic] Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={"error": response.text})
            openai_resp = _anthropic_response_to_openai(response.json(), requested_label)
            return JSONResponse(content=openai_resp)
    except Exception as e:
        logger.exception("[Chat->Anthropic] Critical error")
        return JSONResponse(status_code=500, content={"error": str(e)})

async def _agentrouter_anthropic_messages(request, payload, requested_label):
    """Pass an Anthropic /v1/messages request through to agentrouter's Anthropic endpoint."""
    target_url = _agentrouter_anthropic_base_url()
    headers = {
        "Authorization": f"Bearer {BACKENDS['AGENTROUTER']['key']}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    logger.info(f"[{_req_id(request)}] [Anthropic->Anthropic] Pass-through {requested_label} to {target_url}")

    is_stream = payload.get("stream", False)
    try:
        if is_stream:
            try:
                resp = await _async_post_stream(target_url, payload, headers)
            except httpx.RequestError as e:
                logger.error(f"[Anthropic->Anthropic] Upstream connection failed: {e}")
                return JSONResponse(status_code=502, content={
                    "type": "error",
                    "error": {"type": "api_error", "message": str(e)}
                })

            if resp.status_code != 200:
                err_text = await resp.aread()
                await resp.aclose()
                logger.error(f"[Anthropic->Anthropic] Upstream error ({resp.status_code}): {err_text}")
                return JSONResponse(status_code=resp.status_code, content={
                    "type": "error",
                    "error": {"type": "api_error", "message": _upstream_error_message(err_text)}
                })

            async def passthrough_stream_generator():
                try:
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            return
                        yield (line + "\n").encode('utf-8')
                finally:
                    await resp.aclose()

            return StreamingResponse(passthrough_stream_generator(), media_type="text/event-stream")
        else:
            response = _post_with_retry(target_url, payload, headers)
            if response.status_code != 200:
                logger.error(f"[Anthropic->Anthropic] Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={
                    "type": "error",
                    "error": {"type": "api_error", "message": response.text}
                })
            return JSONResponse(content=response.json())
    except Exception as e:
        logger.exception("[Anthropic->Anthropic] Critical error")
        return JSONResponse(status_code=500, content={
            "type": "error",
            "error": {"type": "api_error", "message": str(e)}
        })

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    requested_label = payload.get("model")

    logger.info(f"[{_req_id(request)}] [Chat] Incoming request for model: '{requested_label}'")

    model_obj = _resolve_model(requested_label)
    if model_obj is None:
        logger.warning(f"Rejected: Model '{requested_label}' not recognized.")
        return JSONResponse(status_code=400, content={"error": f"Model '{requested_label}' not recognized."})
    backend = model_obj["backend"]
    actual_model_id = model_obj["id"]
    backend_url = BACKENDS[backend]["url"]
    backend_key = BACKENDS[backend]["key"]

    logger.info(f"[{_req_id(request)}] Translating label to ID: '{actual_model_id}' via {backend} API")
    payload["model"] = actual_model_id
    payload = _normalize_openai_messages(payload)

    if backend == "AGENTROUTER" and _is_anthropic_model(requested_label):
        return await _agentrouter_anthropic_chat(request, payload, requested_label)

    for key in STRIP_PARAMS_BY_BACKEND.get(backend, []):
        if key in payload:
            logger.debug(f"Stripping unsupported param '{key}' for backend {backend}")
            del payload[key]

    logger.info(f"[{_req_id(request)}] Proxying to {backend_url}")

    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }

    try:
        is_stream = payload.get("stream", False)

        if is_stream:
            try:
                resp = await _async_post_stream(
                    f"{backend_url}/chat/completions",
                    payload,
                    headers,
                )
            except httpx.RequestError as e:
                logger.error(f"Upstream connection failed: {e}")
                return JSONResponse(status_code=502, content={"error": str(e)})

            if resp.status_code != 200:
                err_text = await resp.aread()
                await resp.aclose()
                logger.error(f"Upstream error ({resp.status_code}): {err_text}")
                return JSONResponse(status_code=resp.status_code, content={
                    "error": _upstream_error_message(err_text)
                })

            async def stream_generator():
                first_chunk_sent = False
                stream_id = f"chatcmpl-bridge-{int(time.time())}"
                stream_created = int(time.time())
                accumulated = ""
                retry_count = 0
                current_resp = resp
                try:
                    while True:
                        stream_ended_cleanly = False
                        try:
                            reads = asyncio.Queue()

                            # Pump: consume the upstream stream without ever being
                            # cancelled by the keepalive timeout below (cancelling
                            # wait_for on aiter_lines().__anext__() would close the
                            # httpcore connection, killing the stream on every ping).
                            async def _pump():
                                try:
                                    async for line in current_resp.aiter_lines():
                                        await reads.put(line)
                                except (httpx.RequestError, ConnectionError) as e:
                                    logger.warning(
                                        f"[{_req_id(request)}] Stream dropped mid-iteration: {e}"
                                    )
                                except Exception as e:
                                    logger.debug(f"Stream read error: {e}")
                                finally:
                                    await reads.put(_STREAM_END)

                            pump_task = asyncio.create_task(_pump())
                            try:
                                while True:
                                    try:
                                        line = await asyncio.wait_for(
                                            reads.get(),
                                            timeout=STREAM_KEEPALIVE_INTERVAL,
                                        )
                                    except asyncio.TimeoutError:
                                        if await request.is_disconnected():
                                            return
                                        yield b": ping\n\n"
                                        continue
                                    if line is _STREAM_END:
                                        break
                                    if await request.is_disconnected():
                                        return
                                    if not line:
                                        continue
                                    if not line.startswith("data: "):
                                        yield line.encode('utf-8') + b"\n"
                                        continue
                                    data_str = line[6:]
                                    if data_str.strip() == "[DONE]":
                                        stream_ended_cleanly = True
                                        break
                                    try:
                                        chunk_json = json.loads(data_str)
                                        if not chunk_json.get("choices") or len(chunk_json["choices"]) == 0:
                                            continue
                                        choice = chunk_json["choices"][0]
                                        if "delta" not in choice:
                                            choice["delta"] = {}
                                        chunk_json["id"] = stream_id
                                        chunk_json["created"] = stream_created
                                        chunk_json["object"] = "chat.completion.chunk"
                                        chunk_json["model"] = requested_label
                                        if not first_chunk_sent:
                                            if "role" not in choice["delta"]:
                                                choice["delta"]["role"] = "assistant"
                                            first_chunk_sent = True
                                        else:
                                            if "role" in choice["delta"]:
                                                del choice["delta"]["role"]
                                        delta_content = _chunk_text(choice)
                                        delta_text = ""
                                        if isinstance(delta_content, str):
                                            delta_text = delta_content
                                            accumulated += delta_content
                                        elif isinstance(delta_content, list):
                                            delta_text = "".join(
                                                part.get("text", "") if isinstance(part, dict) else str(part)
                                                for part in delta_content
                                            )
                                            accumulated += delta_text
                                        if delta_text:
                                            logger.info(
                                                f"[{_req_id(request)}] [Stream] token: {delta_text!r}"
                                            )
                                        yield f"data: {json.dumps(chunk_json)}\n\n".encode('utf-8')
                                    except json.JSONDecodeError:
                                        continue
                                    except Exception as e:
                                        logger.debug(f"Chunk processing error: {e}")
                                        continue
                            finally:
                                pump_task.cancel()
                                await asyncio.gather(pump_task, return_exceptions=True)
                        finally:
                            await current_resp.aclose()

                        if stream_ended_cleanly:
                            break

                        if not accumulated or retry_count >= MAX_STREAM_CONTINUATIONS:
                            break

                        retry_count += 1
                        logger.info(
                            f"[{_req_id(request)}] Stream dropped after {len(accumulated)} chars, "
                            f"retrying continuation attempt {retry_count}/{MAX_STREAM_CONTINUATIONS}"
                        )
                        continuation_messages = list(payload.get("messages", []))
                        continuation_messages.append({"role": "assistant", "content": accumulated})
                        continuation_messages.append({"role": "user", "content": STREAM_CONTINUATION_NUDGE})
                        continuation_payload = dict(payload)
                        continuation_payload["messages"] = continuation_messages
                        continuation_payload["stream"] = True
                        try:
                            current_resp = await _async_post_stream(
                                f"{backend_url}/chat/completions",
                                continuation_payload,
                                headers,
                            )
                        except httpx.RequestError as e:
                            logger.error(f"Continuation request failed: {e}")
                            break
                        if current_resp.status_code != 200:
                            err_text = await current_resp.aread()
                            logger.error(f"Continuation upstream error ({current_resp.status_code}): {err_text}")
                            break

                    yield b"data: [DONE]\n\n"
                finally:
                    await current_resp.aclose()

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            response = _post_with_retry(f"{backend_url}/chat/completions", payload, headers)
            if response.status_code != 200:
                logger.error(f"Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={"error": response.text})
            resp_json = response.json()
            resp_json["model"] = requested_label
            return JSONResponse(content=resp_json)
    except Exception as e:
        logger.exception(f"Critical error during proxying")
        return JSONResponse(status_code=500, content={"error": str(e)})

def generate_opencode_config(selected_models=None, do_test=False, top_n=None, targets=None):
    global MODEL_MAP, CLAUDE_MODEL_MAP, ACTIVE_MODELS
    MODEL_MAP.clear()
    CLAUDE_MODEL_MAP.clear()

    pre_test_models = []

    if selected_models is not None:
        for m in selected_models:
            MODEL_MAP[m["label"]] = m
        pre_test_models = selected_models
        all_models = selected_models
    else:
        all_models = get_all_models()
        if not all_models:
            logger.error("No models fetched. Cannot generate config.")
            return
        for m in all_models:
            MODEL_MAP[m["label"]] = m
    CLAUDE_MODEL_MAP.clear()
    seen_short = set()
    for label, m in MODEL_MAP.items():
        short = label.split(":")[-1].split("/")[-1]
        claude_name = f"claude-{short}"
        if claude_name not in seen_short and claude_name != label:
            seen_short.add(claude_name)
            CLAUDE_MODEL_MAP[claude_name] = m
    if top_n is not None:
        if top_n == -1:
            g4f_top = [m for m in all_models if m["backend"] == "G4F"][:15]
            eaon_top = [m for m in all_models if m["backend"] == "EAON" and m.get("tier") == "plus"]
            pa_models = [m for m in all_models if m["backend"] == "PA"]
            logger.info(f"Selecting Top 15 G4F models, {len(eaon_top)} EAON plus-tier, and {len(pa_models)} PA models.")
        else:
            g4f_top = [m for m in all_models if m["backend"] == "G4F"][:top_n]
            eaon_top = [m for m in all_models if m["backend"] == "EAON" and m.get("tier") == "plus"][:top_n]
            pa_models = [m for m in all_models if m["backend"] == "PA"][:top_n]
            logger.info(f"Selecting Top {top_n} G4F, {top_n} EAON plus-tier, and {top_n} PA models.")
        pre_test_models = g4f_top + eaon_top + pa_models
    else:
        pre_test_models = all_models

    final_models = []
    if do_test:
        logger.info("\nRunning live tests on selected models...")
        for m in pre_test_models:
            if test_model_live(m):
                final_models.append(m)
        logger.info(f"{len(final_models)} out of {len(pre_test_models)} passed testing.")
    else:
        final_models = pre_test_models

    if not final_models:
        logger.error("No valid models to save. Exiting.")
        sys.exit(1)

    ACTIVE_MODELS.clear()
    for m in final_models:
        ACTIVE_MODELS.add(m["label"])

    logger.info(f"Saving {len(final_models)} models...")

    if targets is None:
        targets = ["opencode"]

    for target in targets:
        if target == "opencode":
            continue
        conflicts = _detect_config_conflicts(target, final_models)
        if conflicts:
            logger.info(f"Conflict detection for {target}:")
            for c in conflicts:
                logger.info(c)

    target_map = {
        "opencode": opencode,
        "claude-code": claude_code,
        "codex": codex,
        "cursor": cursor,
        "antigravity": antigravity,
    }

    for target in targets:
        module = target_map.get(target)
        if module:
            module.write_config(final_models, top_n if target == "opencode" else None)

def cli_main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description="G4F Bridge - Multi-tool API bridge for G4F, EAON, and PA proxy networks",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--keys", action="store_true",
                        help="Manage API keys (G4F, EAON)")
    parser.add_argument("-l", "--list", nargs='?', const=-1, default=None, type=int,
                        help="List models from all providers. Optionally limit count (e.g. -l 10)")
    parser.add_argument("-m", "--model", type=str, nargs="+",
                        help="Search for models matching terms (e.g. -m gpt deepseek)")
    parser.add_argument("-t", "--test", action="store_true",
                        help="Test selected models before adding them")
    parser.add_argument("-b", "--best", nargs='?', const=-1, default=None, type=int,
                        help="Extract top N G4F models and EAON plus-tier models")
    parser.add_argument("-s", "--setup", action="store_true",
                        help="Run API key setup wizard")
    parser.add_argument("--target", nargs="+", choices=TARGET_CHOICES, default=None,
                        help=f"Target tools (default: all installed tools). Choices: {', '.join(TARGET_CHOICES)}")
    args = parser.parse_args()

    if args.keys:
        manage_keys()
        return

    load_or_prompt_keys(force_setup=args.setup)

    if args.list is not None:
        all_models = get_all_models()
        if not all_models:
            logger.error("No models available.")
            sys.exit(1)
        limit = None if args.list == -1 else args.list
        if limit is not None and limit < len(all_models):
            print(f"\nTop {limit} models (sorted by popularity):\n")
            models_to_show = all_models[:limit]
        else:
            print(f"\nAll {len(all_models)} models (sorted by popularity):\n")
            models_to_show = all_models
        for i, m in enumerate(models_to_show, 1):
            backend_tag = m.get("backend", "?")
            label = m.get("label", m.get("id", "?"))
            display = label.split(":")[-1].split("/")[-1]
            print(f"  {i:>3}. [{backend_tag}] {display:35s} {m.get('requests', 0):>8,} requests")
        print()
        sys.exit(0)

    if args.target is None:
        targets = _detect_installed_targets() or ["opencode"]
    elif "all" in args.target:
        targets = ALL_TARGETS
    else:
        targets = args.target

    if args.setup:
        if not args.model and args.best is None:
            sys.exit(0)

    if args.model or args.best is not None or not args.setup:
        if args.model or args.best is not None:
            logger.info("Running pre-flight checks...")
            _run_preflight_checks(targets)

    if args.model:
        all_models = get_all_models()
        if not all_models:
            sys.exit(1)
        selected = interactive_model_selection(args.model, all_models)
        if not selected:
            logger.warning("No models selected. Exiting.")
            sys.exit(0)
        generate_opencode_config(selected_models=selected, do_test=args.test, targets=targets)
    elif args.best is not None:
        generate_opencode_config(top_n=args.best, do_test=args.test, targets=targets)
    else:
        all_models = get_all_models()
        if not all_models:
            logger.error("No models available from any backend.")
            sys.exit(1)
        print(f"\nAvailable models ({len(all_models)} total):\n")
        for i, m in enumerate(all_models[:30], 1):
            backend_tag = m.get("backend", "?")
            label = m.get("label", m.get("id", "?"))
            display = label.split(":")[-1].split("/")[-1]
            print(f"  {i:>3}. [{backend_tag}] {display:35s} {m.get('requests', 0):>8,} requests")
        if len(all_models) > 30:
            print(f"\n  ... and {len(all_models) - 30} more (use -l to list all)")
        print()

        logger.info(f"Generating config for {len(all_models)} models")
        generate_opencode_config(selected_models=all_models, do_test=False, targets=targets)

    print(f"\nStarting Bridge on http://127.0.0.1:{PORT} ...")
    logger.info(f"Bridge running on http://127.0.0.1:{PORT}")
    logger.info(f"Concurrent request limit: 50")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

if __name__ == "__main__":
    cli_main()
