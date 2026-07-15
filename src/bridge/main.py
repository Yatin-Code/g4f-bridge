import json
import sys
import time
import uuid
import argparse
import traceback
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from .utils import (
    BACKENDS, STRIP_PARAMS_BY_BACKEND,
    ALL_TARGETS, TARGET_CHOICES,
    load_or_prompt_keys,
    _run_preflight_checks,
    _detect_config_conflicts,
    _safe_write_json,
)
from .models import (
    MODEL_MAP, CLAUDE_MODEL_MAP, ACTIVE_MODELS,
    get_all_models, interactive_model_selection, test_model_live,
    _resolve_model,
)
from .translate import (
    _post_with_retry,
    _anthropic_to_openai,
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

PORT = 1337

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Model Discovery ----

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


# ---- Anthropic Messages API ----

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

    print(f"\n{'='*50}")
    print(f"[Anthropic] Incoming request for model: '{requested_label}'")

    if "thinking" in payload:
        print(f"  -> Stripping 'thinking' field")
        del payload["thinking"]
    if "context_management" in payload:
        print(f"  -> Stripping 'context_management' field")
        del payload["context_management"]
    if "output_config" in payload:
        print(f"  -> Stripping 'output_config' field")
        del payload["output_config"]

    model_obj = _resolve_model(requested_label)
    if model_obj is None:
        print(f"Rejected: Model '{requested_label}' is not recognized.")
        return JSONResponse(status_code=400, content={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": f"Model '{requested_label}' not recognized."}
        })
    backend = model_obj["backend"]
    actual_model_id = model_obj["id"]
    backend_url = BACKENDS[backend]["url"]
    backend_key = BACKENDS[backend]["key"]

    openai_payload = _anthropic_to_openai(payload)
    openai_payload["model"] = actual_model_id

    for key in STRIP_PARAMS_BY_BACKEND.get(backend, []):
        if key in openai_payload:
            del openai_payload[key]

    print(f"[Anthropic] Translated to OpenAI format, proxying to {backend}...")

    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }

    is_stream = payload.get("stream", False)
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    try:
        if is_stream:
            openai_payload["stream"] = True
            upstream_req = _post_with_retry(
                f"{backend_url}/chat/completions",
                openai_payload,
                headers,
                stream=True,
                timeout=(25, 600)
            )

            if upstream_req.status_code != 200:
                err_text = upstream_req.text
                print(f"Upstream error ({upstream_req.status_code}): {err_text}")
                return JSONResponse(status_code=upstream_req.status_code, content={
                    "type": "error",
                    "error": {"type": "api_error", "message": err_text}
                })

            def anthropic_stream_generator():
                print("Streaming Anthropic-format response back to Claude Code...")
                is_first = True
                stream_ended = False
                try:
                    for line in upstream_req.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode('utf-8')
                        if not decoded.startswith("data: "):
                            continue
                        data_str = decoded[6:].strip()
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
                finally:
                    if not stream_ended:
                        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode('utf-8')
                    upstream_req.close()

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
        print(f"CRITICAL ERROR during Anthropic proxying:")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "type": "error",
            "error": {"type": "api_error", "message": str(e)}
        })


# ---- Gemini API (for Antigravity) ----

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

    print(f"\n{'='*50}")
    print(f"[Gemini] Incoming request for model: '{model_label}'")

    model_obj = _resolve_model(model_label)
    if model_obj is None:
        print(f"[Gemini] Model '{model_label}' not found. Attempting fallback routing...")
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

        print(f"  -> Routed '{model_label}' to fallback bridge model: '{fallback_obj['id']}'")
        model_obj = fallback_obj

    backend = model_obj["backend"]
    actual_model_id = model_obj["id"]
    backend_url = BACKENDS[backend]["url"]
    backend_key = BACKENDS[backend]["key"]

    openai_payload = _gemini_to_openai(payload, actual_model_id)

    for key in STRIP_PARAMS_BY_BACKEND.get(backend, []):
        if key in openai_payload:
            del openai_payload[key]

    print(f"[Gemini] Translated to OpenAI format, proxying to {backend}...")

    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }

    try:
        if stream:
            openai_payload["stream"] = True
            upstream_req = _post_with_retry(
                f"{backend_url}/chat/completions",
                openai_payload,
                headers,
                stream=True,
                timeout=(25, 600)
            )

            if upstream_req.status_code != 200:
                err_text = upstream_req.text
                print(f"[Gemini] Upstream error ({upstream_req.status_code}): {err_text}")
                return JSONResponse(status_code=upstream_req.status_code, content={
                    "error": {"code": upstream_req.status_code, "message": err_text, "status": "UPSTREAM_ERROR"}
                })

            def gemini_stream_generator():
                print("[Gemini] Streaming response back to Antigravity...")
                msg_id = f"msg_{uuid.uuid4().hex[:24]}"
                try:
                    for line in upstream_req.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode('utf-8')
                        if not decoded.startswith("data: "):
                            continue
                        data_str = decoded[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(data_str)
                            gemini_chunk = _openai_chunk_to_gemini_chunk(chunk_json, msg_id)
                            if gemini_chunk:
                                yield f"data: {json.dumps(gemini_chunk)}\n\n".encode('utf-8')
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue
                finally:
                    yield b"data: [DONE]\n\n"
                    upstream_req.close()

            return StreamingResponse(gemini_stream_generator(), media_type="text/event-stream")

        else:
            openai_payload["stream"] = False
            response = _post_with_retry(f"{backend_url}/chat/completions", openai_payload, headers)
            if response.status_code != 200:
                print(f"[Gemini] Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={
                    "error": {"code": response.status_code, "message": response.text, "status": "UPSTREAM_ERROR"}
                })
            gemini_resp = _openai_to_gemini_response(response.json(), model_label)
            print("[Gemini] Non-streaming response sent.")
            return JSONResponse(content=gemini_resp)

    except Exception as e:
        print(f"[Gemini] CRITICAL ERROR:")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "error": {"code": 500, "message": str(e), "status": "INTERNAL_ERROR"}
        })


# ---- Responses API (for Codex CLI) ----

@app.post("/v1/responses")
async def responses_create(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "error": {"message": "Invalid JSON body", "type": "invalid_request_error"}
        })

    requested_label = payload.get("model", "")

    print(f"\n{'='*50}")
    print(f"[Responses] Incoming request for model: '{requested_label}'")

    model_obj = _resolve_model(requested_label)
    if model_obj is None:
        print(f"[Responses] Rejected: Model '{requested_label}' is not recognized.")
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

    print(f"[Responses] Translated to OpenAI format, proxying to {backend}...")

    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }

    is_stream = payload.get("stream", False)
    resp_id = f"resp_{uuid.uuid4().hex[:24]}"

    try:
        if is_stream:
            openai_payload["stream"] = True
            upstream_req = _post_with_retry(
                f"{backend_url}/chat/completions",
                openai_payload,
                headers,
                stream=True,
                timeout=(25, 600)
            )

            if upstream_req.status_code != 200:
                err_text = upstream_req.text
                print(f"[Responses] Upstream error ({upstream_req.status_code}): {err_text}")
                return JSONResponse(status_code=upstream_req.status_code, content={
                    "error": {"message": err_text, "type": "api_error"}
                })

            def responses_stream_generator():
                print("[Responses] Streaming response back to Codex CLI...")
                is_first = True
                try:
                    for line in upstream_req.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode('utf-8')
                        if not decoded.startswith("data: "):
                            continue
                        data_str = decoded[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(data_str)
                            events = _openai_chunk_to_responses_events(chunk_json, resp_id, is_first)
                            if events:
                                is_first = False
                            for event in events:
                                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode('utf-8')
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue
                finally:
                    yield b"data: [DONE]\n\n"
                    upstream_req.close()

            return StreamingResponse(responses_stream_generator(), media_type="text/event-stream")

        else:
            openai_payload["stream"] = False
            response = _post_with_retry(f"{backend_url}/chat/completions", openai_payload, headers)
            if response.status_code != 200:
                print(f"[Responses] Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={
                    "error": {"message": response.text, "type": "api_error"}
                })
            responses_resp = _openai_to_responses_response(response.json())
            print("[Responses] Non-streaming response sent.")
            return JSONResponse(content=responses_resp)

    except Exception as e:
        print(f"[Responses] CRITICAL ERROR:")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "error": {"message": str(e), "type": "api_error"}
        })


# ---- OpenAI Chat Completions (for all tools) ----

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    requested_label = payload.get("model")

    print(f"\n{'='*50}")
    print(f"Incoming request for model: '{requested_label}'")

    model_obj = _resolve_model(requested_label)
    if model_obj is None:
        print(f"Rejected: Model '{requested_label}' is not recognized.")
        return JSONResponse(status_code=400, content={"error": f"Model '{requested_label}' not recognized."})
    backend = model_obj["backend"]
    actual_model_id = model_obj["id"]
    backend_url = BACKENDS[backend]["url"]
    backend_key = BACKENDS[backend]["key"]

    print(f"Translating label to ID: '{actual_model_id}' via {backend} API")
    payload["model"] = actual_model_id

    for key in STRIP_PARAMS_BY_BACKEND.get(backend, []):
        if key in payload:
            print(f"    -> Stripping unsupported param '{key}' for backend {backend}")
            del payload[key]

    print(f"Proxying request directly to {backend_url} ...")

    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }

    try:
        is_stream = payload.get("stream", False)

        if is_stream:
            upstream_req = _post_with_retry(
                f"{backend_url}/chat/completions",
                payload,
                headers,
                stream=True,
                timeout=(25, 600)
            )

            if upstream_req.status_code != 200:
                err_text = upstream_req.text
                print(f"Upstream error ({upstream_req.status_code}): {err_text}")
                return JSONResponse(status_code=upstream_req.status_code, content={"error": err_text})

            def stream_generator():
                print("Streaming response back (with strict validation)...")
                first_chunk_sent = False
                stream_ended_cleanly = False

                stream_id = f"chatcmpl-bridge-{int(time.time())}"
                stream_created = int(time.time())

                try:
                    for line in upstream_req.iter_lines():
                        if not line:
                            yield b"\n"
                            continue

                        decoded_line = line.decode('utf-8')
                        if not decoded_line.startswith("data: "):
                            yield line + b"\n"
                            continue

                        data_str = decoded_line[6:]

                        if data_str.strip() == "[DONE]":
                            print("  [STREAM] End of stream received [DONE]")
                            stream_ended_cleanly = True
                            yield b"data: [DONE]\n\n"
                            break

                        try:
                            chunk_json = json.loads(data_str)
                            print(f"  [RAW] {data_str[:120]}...")

                            if not chunk_json.get("choices") or len(chunk_json["choices"]) == 0:
                                print("    -> Skipping bad chunk: 'choices' array is empty")
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
                                    print("    -> Injecting 'role': 'assistant' into first chunk")
                                    choice["delta"]["role"] = "assistant"
                                first_chunk_sent = True
                            else:
                                if "role" in choice["delta"]:
                                    del choice["delta"]["role"]

                            fixed_data_str = json.dumps(chunk_json)
                            print(f"  [FIX] {fixed_data_str[:120]}...")

                            yield f"data: {fixed_data_str}\n\n".encode('utf-8')

                        except json.JSONDecodeError:
                            print(f"  [ERR] Failed to parse JSON: {data_str}")
                            yield line + b"\n\n"
                        except Exception as e:
                            print(f"  [ERR] Exception during chunk processing: {e}")
                            yield line + b"\n\n"
                finally:
                    if not stream_ended_cleanly:
                        print("Upstream closed without sending [DONE]")
                        yield b"data: [DONE]\n\n"
                    upstream_req.close()

            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        else:
            response = _post_with_retry(f"{backend_url}/chat/completions", payload, headers)
            if response.status_code != 200:
                print(f"Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={"error": response.text})

            print("Response successfully retrieved!")
            resp_json = response.json()
            resp_json["model"] = requested_label
            return JSONResponse(content=resp_json)

    except Exception as e:
        print(f"CRITICAL ERROR during proxying:")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---- Config Generation (orchestration) ----

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
            print("No models fetched. Cannot generate config.")
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
                print(f"Selecting Top 15 models from G4F and ALL {len(eaon_top)} plus-tier models from EAON.")
            else:
                g4f_top = [m for m in all_models if m["backend"] == "G4F"][:top_n]
                eaon_top = [m for m in all_models if m["backend"] == "EAON" and m.get("tier") == "plus"][:top_n]
                print(f"Selecting Top {top_n} models from G4F and Top {top_n} plus-tier models from EAON.")
            pre_test_models = g4f_top + eaon_top
        else:
            pre_test_models = all_models

    final_models = []
    if do_test:
        print("\nRunning live tests on selected models...")
        for m in pre_test_models:
            if test_model_live(m):
                final_models.append(m)
        print(f"{len(final_models)} out of {len(pre_test_models)} passed the test.")
    else:
        final_models = pre_test_models

    if not final_models:
        print("No valid models to save. Exiting.")
        sys.exit(1)

    ACTIVE_MODELS.clear()
    for m in final_models:
        ACTIVE_MODELS.add(m["label"])

    print(f"Saving {len(final_models)} models...")

    if targets is None:
        targets = ["opencode"]

    has_conflicts = False
    for target in targets:
        if target == "opencode":
            continue
        conflicts = _detect_config_conflicts(target, final_models)
        if conflicts:
            has_conflicts = True
            print(f"\nConflict detection for {target}:")
            for c in conflicts:
                print(c)

    if has_conflicts:
        print()

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


# ---- TUI Launch ----

TUI_SCREEN_COMMANDS = {"setup", "config", "dashboard", "logs", "models"}
CLI_FLAGS = {"-l", "--list", "-m", "--model", "-t", "--test", "-b", "--best", "-s", "--setup", "--target"}


def _launch_tui(extra_args=None):
    import subprocess
    from pathlib import Path

    bridge_dir = Path(__file__).resolve().parents[2]
    tui_dist = bridge_dir / "tui" / "dist" / "index.js"
    if not tui_dist.exists():
        print("TUI not built. Run: cd tui && npm run build")
        sys.exit(1)

    cmd = ["node", str(tui_dist)] + (extra_args or [])
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def _has_cli_flags():
    """Check if sys.argv contains any Python CLI flags."""
    for arg in sys.argv[1:]:
        if arg in CLI_FLAGS or arg.startswith("-"):
            return True
    return False


def _is_tui_command():
    """Check if sys.argv[1] is a TUI screen command."""
    if len(sys.argv) > 1 and sys.argv[1] in TUI_SCREEN_COMMANDS:
        return True
    return False


# ---- CLI Entry Point ----

def cli_main():
    if _is_tui_command():
        _launch_tui(sys.argv[2:])

    if _has_cli_flags():
        _run_python_cli()
        return

    if len(sys.argv) > 1:
        _launch_tui(sys.argv[1:])

    _launch_tui()


def _run_python_cli():
    parser = argparse.ArgumentParser(description="G4F/EAON Bridge with multi-tool config generation")
    parser.add_argument("-l", "--list", nargs='?', const=-1, default=None, type=int,
                        help="List models from all proxies. Optionally limit count (e.g. -l 10)")
    parser.add_argument("-m", "--model", type=str, nargs="+", help="Search for models matching one or more terms (e.g. -m gpt deepseek)")
    parser.add_argument("-t", "--test", action="store_true", help="Test the selected models before adding them")
    parser.add_argument("-b", "--best", nargs='?', const=-1, default=None, type=int, help="Extract top N models from G4F (defaults to 15) and plus-tier models from EAON")
    parser.add_argument("-s", "--setup", action="store_true", help="Run the API key setup wizard to update keys")
    parser.add_argument("--target", nargs="+", choices=TARGET_CHOICES, default=None,
                        help=f"Target tools to generate configs for (default: opencode). "
                             f"Choices: {', '.join(TARGET_CHOICES)}. "
                             f"Use '--target all' to target all tools.")
    args = parser.parse_args()

    load_or_prompt_keys(force_setup=args.setup)

    # List-only mode: fetch and print models, then exit
    if args.list is not None:
        all_models = get_all_models()
        if not all_models:
            print("No models available.")
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
            print(f"  {i:>3}. [{backend_tag}] {display:30s} {m.get('requests', 0):>8,} requests")
        print()
        sys.exit(0)

    if args.target is None:
        targets = ["opencode"]
    elif "all" in args.target:
        targets = ALL_TARGETS
    else:
        targets = args.target

    if args.setup:
        if not args.model and args.best is None:
            sys.exit(0)

    if args.model or args.best is not None or not args.setup:
        print("Running pre-flight checks...")
        _run_preflight_checks(targets)
        print("   All checks passed.\n")

    if args.model:
        all_models = get_all_models()
        if not all_models:
            sys.exit(1)
        selected = interactive_model_selection(args.model, all_models)
        if not selected:
            print("No models selected. Exiting.")
            sys.exit(0)
        generate_opencode_config(selected_models=selected, do_test=args.test, targets=targets)
    else:
        generate_opencode_config(top_n=args.best, do_test=args.test, targets=targets)

    print(f"\nStarting Bridge on http://127.0.0.1:{PORT}...")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    cli_main()
