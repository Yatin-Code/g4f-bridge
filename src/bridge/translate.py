import json
import time
import uuid
import requests


def _post_with_retry(url, json_payload, headers, stream=False, timeout=None, max_retries=2):
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=json_payload, headers=headers, stream=stream, timeout=timeout)
            if resp.status_code in (429, 502, 503, 504) and attempt < max_retries:
                wait = 1.5 * (attempt + 1)
                print(f"    -> Upstream returned {resp.status_code}, retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})...")
                resp.close()
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < max_retries:
                wait = 1.5 * (attempt + 1)
                print(f"    -> Request failed ({e}), retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
    if last_exc:
        raise last_exc
    return resp


def _anthropic_to_openai(payload):
    messages = []
    system = payload.get("system")
    if system:
        if isinstance(system, list):
            system_text = " ".join(
                block.get("text", "") for block in system
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            system_text = str(system)
        if system_text.strip():
            messages.append({"role": "system", "content": system_text})

    for msg in payload.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")

        if isinstance(content, str):
            messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            text_parts = []
            tool_calls = []
            tool_results = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}))
                        }
                    })
                elif btype == "tool_result":
                    tool_results.append({
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": block.get("content", "") if isinstance(block.get("content"), str)
                                   else json.dumps(block.get("content", ""))
                    })

            if tool_results:
                for tr in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "content": tr["content"]
                    })
            elif tool_calls:
                assistant_msg = {"role": role, "content": " ".join(text_parts) if text_parts else None}
                assistant_msg["tool_calls"] = tool_calls
                messages.append(assistant_msg)
            else:
                messages.append({"role": role, "content": " ".join(text_parts) if text_parts else ""})

    openai_tools = []
    for tool in payload.get("tools", []):
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "custom" or "input_schema" in tool:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}})
                }
            })
        elif "function" in tool:
            openai_tools.append(tool)

    tool_choice = None
    tc = payload.get("tool_choice")
    if tc:
        if isinstance(tc, dict):
            tc_type = tc.get("type", "auto")
            if tc_type == "any":
                tool_choice = "required"
            elif tc_type == "tool":
                tool_choice = {"type": "function", "function": {"name": tc.get("name", "")}}
            elif tc_type == "none":
                tool_choice = "none"
            else:
                tool_choice = "auto"

    result = {
        "model": payload.get("model", ""),
        "messages": messages,
        "stream": payload.get("stream", False),
    }

    if payload.get("max_tokens"):
        result["max_tokens"] = payload["max_tokens"]
    if payload.get("temperature") is not None:
        result["temperature"] = payload["temperature"]
    if payload.get("stop_sequences"):
        result["stop"] = payload["stop_sequences"]
    if openai_tools:
        result["tools"] = openai_tools
    if tool_choice:
        result["tool_choice"] = tool_choice

    return result


def _openai_chunk_to_anthropic_events(chunk_json, msg_id, model_name, is_first):
    events = []
    choices = chunk_json.get("choices", [])
    if not choices:
        return events, is_first

    choice = choices[0]
    delta = choice.get("delta", {})

    if is_first:
        events.append({
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model_name,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0}
            }
        })
        is_first = False

    if delta.get("content"):
        events.append({
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""}
        })
        events.append({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": delta["content"]}
        })

    if delta.get("tool_calls"):
        for tc in delta["tool_calls"]:
            tc_id = tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
            fn = tc.get("function", {})
            if tc.get("function", {}).get("name"):
                events.append({
                    "type": "content_block_start",
                    "index": len(events),
                    "content_block": {
                        "type": "tool_use",
                        "id": tc_id,
                        "name": fn.get("name", ""),
                        "input": {}
                    }
                })
            if fn.get("arguments"):
                events.append({
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": fn.get("arguments", "")
                    }
                })

    finish = choice.get("finish_reason")
    if finish:
        stop_reason = "end_turn"
        if finish == "tool_calls":
            stop_reason = "tool_use"
        elif finish == "stop":
            stop_reason = "end_turn"
        elif finish == "length":
            stop_reason = "max_tokens"

        events.append({"type": "content_block_stop", "index": 0})
        events.append({
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 0}
        })
        events.append({"type": "message_stop"})

    return events, is_first


def _openai_response_to_anthropic(resp_json, model_name):
    content = []
    choice = resp_json.get("choices", [{}])[0]
    message = choice.get("message", {})

    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})

    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            content.append({
                "type": "tool_use",
                "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                "name": tc.get("function", {}).get("name", ""),
                "input": json.loads(tc.get("function", {}).get("arguments", "{}"))
            })

    finish = choice.get("finish_reason", "stop")
    stop_reason = "end_turn"
    if finish == "tool_calls":
        stop_reason = "tool_use"
    elif finish == "length":
        stop_reason = "max_tokens"

    usage = resp_json.get("usage", {})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0)
        }
    }


def _gemini_to_openai(payload, model_id):
    messages = []

    sys_inst = payload.get("systemInstruction")
    if sys_inst:
        parts = sys_inst.get("parts", [])
        sys_text = " ".join(p.get("text", "") for p in parts if p.get("text"))
        if sys_text.strip():
            messages.append({"role": "system", "content": sys_text})

    for content in payload.get("contents", []):
        role = content.get("role", "user")
        parts = content.get("parts", [])
        openai_role = "assistant" if role == "model" else role

        text_parts = []
        tool_calls = []
        tool_results = []

        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("text"):
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {}))
                    }
                })
            elif "functionResponse" in part:
                fr = part["functionResponse"]
                tool_results.append({
                    "tool_call_id": f"call_{uuid.uuid4().hex[:24]}",
                    "content": json.dumps(fr.get("response", {}))
                })

        if tool_results:
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"]
                })
        elif tool_calls:
            msg = {"role": openai_role, "content": " ".join(text_parts) if text_parts else None}
            msg["tool_calls"] = tool_calls
            messages.append(msg)
        else:
            messages.append({"role": openai_role, "content": " ".join(text_parts) if text_parts else ""})

    openai_tools = []
    for tool in payload.get("tools", []):
        func_decl = tool.get("functionDeclarations", [])
        for fd in func_decl:
            if not isinstance(fd, dict):
                continue
            params = fd.get("parameters", {"type": "object", "properties": {}})
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": fd.get("name", ""),
                    "description": fd.get("description", ""),
                    "parameters": params
                }
            })

    tool_choice = None
    tc = payload.get("toolConfig", {}).get("functionCallingConfig", {})
    if tc:
        mode = tc.get("mode", "AUTO")
        if mode == "ANY":
            tool_choice = "required"
        elif mode == "NONE":
            tool_choice = "none"
        else:
            tool_choice = "auto"

    gen_cfg = payload.get("generationConfig", {})

    result = {
        "model": model_id,
        "messages": messages,
    }

    if gen_cfg.get("maxOutputTokens"):
        result["max_tokens"] = gen_cfg["maxOutputTokens"]
    if gen_cfg.get("temperature") is not None:
        result["temperature"] = gen_cfg["temperature"]
    if gen_cfg.get("topP") is not None:
        result["top_p"] = gen_cfg["topP"]
    if gen_cfg.get("stopSequences"):
        result["stop"] = gen_cfg["stopSequences"]
    if openai_tools:
        result["tools"] = openai_tools
    if tool_choice:
        result["tool_choice"] = tool_choice

    return result


def _openai_to_gemini_response(resp_json, model_name):
    choice = resp_json.get("choices", [{}])[0]
    message = choice.get("message", {})
    parts = []

    if message.get("content"):
        parts.append({"text": message["content"]})

    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            parts.append({
                "functionCall": {
                    "name": fn.get("name", ""),
                    "args": args
                }
            })

    finish = choice.get("finish_reason", "stop")
    gemini_finish = "STOP"
    if finish == "tool_calls":
        gemini_finish = "STOP"
    elif finish == "length":
        gemini_finish = "MAX_TOKENS"
    elif finish and finish not in ("stop",):
        gemini_finish = "OTHER"

    usage = resp_json.get("usage", {})

    return {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": parts
            },
            "finishReason": gemini_finish
        }],
        "usageMetadata": {
            "promptTokenCount": usage.get("prompt_tokens", 0),
            "candidatesTokenCount": usage.get("completion_tokens", 0),
            "totalTokenCount": usage.get("total_tokens",
                usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
        }
    }


def _openai_chunk_to_gemini_chunk(chunk_json, msg_id):
    choices = chunk_json.get("choices", [])
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    parts = []

    if delta.get("content"):
        parts.append({"text": delta["content"]})

    if delta.get("tool_calls"):
        for tc in delta["tool_calls"]:
            fn = tc.get("function", {})
            fc_part = {"name": fn.get("name", "")}
            if fn.get("arguments"):
                try:
                    fc_part["args"] = json.loads(fn["arguments"])
                except (json.JSONDecodeError, TypeError):
                    fc_part["args"] = {}
            else:
                fc_part["args"] = {}
            parts.append({"functionCall": fc_part})

    finish = choice.get("finish_reason")
    gemini_finish = None
    if finish == "stop":
        gemini_finish = "STOP"
    elif finish == "tool_calls":
        gemini_finish = "STOP"
    elif finish == "length":
        gemini_finish = "MAX_TOKENS"
    elif finish:
        gemini_finish = "OTHER"

    chunk = {}
    if parts:
        chunk["candidates"] = [{
            "content": {"role": "model", "parts": parts},
        }]

    if gemini_finish:
        chunk["candidates"] = chunk.get("candidates", [{"content": {"role": "model", "parts": []}}])
        chunk["candidates"][0]["finishReason"] = gemini_finish

    usage = chunk_json.get("usage")
    if usage:
        chunk["usageMetadata"] = {
            "promptTokenCount": usage.get("prompt_tokens", 0),
            "candidatesTokenCount": usage.get("completion_tokens", 0),
            "totalTokenCount": usage.get("total_tokens",
                usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
        }

    return chunk if chunk else None


def _responses_to_openai(payload):
    messages = []

    instructions = payload.get("instructions")
    if instructions:
        if isinstance(instructions, str):
            messages.append({"role": "system", "content": instructions})
        elif isinstance(instructions, list):
            text = " ".join(p.get("text", "") for p in instructions if isinstance(p, dict))
            if text.strip():
                messages.append({"role": "system", "content": text})

    inp = payload.get("input", "")
    if isinstance(inp, str):
        if inp.strip():
            messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            role = item.get("role", "user")

            if item_type == "message" or role in ("user", "assistant", "system"):
                content = item.get("content", "")
                if isinstance(content, str):
                    messages.append({"role": role, "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") in ("input_text", "output_text", "text"):
                                text_parts.append(part.get("text", ""))
                            elif part.get("type") == "input_image":
                                text_parts.append("[image]")
                    messages.append({"role": role, "content": " ".join(text_parts) if text_parts else ""})
            elif item_type == "function_call":
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item.get("call_id", f"call_{uuid.uuid4().hex[:24]}"),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": item.get("arguments", "{}")
                        }
                    }]
                })
            elif item_type == "function_call_output":
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "content": item.get("output", "")
                })

    openai_tools = []
    for tool in payload.get("tools", []):
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function":
            func = tool.get("function", {})
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {"type": "object", "properties": {}})
                }
            })

    tool_choice = payload.get("tool_choice")

    result = {
        "model": payload.get("model", ""),
        "messages": messages,
    }

    if payload.get("max_output_tokens"):
        result["max_tokens"] = payload["max_output_tokens"]
    if payload.get("temperature") is not None:
        result["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        result["top_p"] = payload["top_p"]
    if payload.get("stop"):
        result["stop"] = payload["stop"]
    if openai_tools:
        result["tools"] = openai_tools
    if tool_choice:
        result["tool_choice"] = tool_choice

    return result


def _openai_to_responses_response(resp_json):
    choice = resp_json.get("choices", [{}])[0]
    message = choice.get("message", {})
    output_items = []

    if message.get("content"):
        output_items.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "status": "completed",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": message["content"]
            }]
        })

    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            fn = tc.get("function", {})
            output_items.append({
                "type": "function_call",
                "id": f"fc_{uuid.uuid4().hex[:24]}",
                "call_id": tc.get("id", f"call_{uuid.uuid4().hex[:24]}"),
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", "{}"),
                "status": "completed"
            })

    finish = choice.get("finish_reason", "stop")
    status = "completed"
    incomplete_reason = None
    if finish == "length":
        incomplete_reason = "max_tokens"

    usage = resp_json.get("usage", {})

    resp_id = f"resp_{uuid.uuid4().hex[:24]}"

    return {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "error": None,
        "incomplete_details": {"reason": incomplete_reason} if incomplete_reason else None,
        "instructions": None,
        "max_output_tokens": None,
        "model": resp_json.get("model", ""),
        "output": output_items,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens",
                usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
        }
    }


def _openai_chunk_to_responses_events(chunk_json, resp_id, is_first):
    events = []
    choices = chunk_json.get("choices", [])
    if not choices:
        return events

    choice = choices[0]
    delta = choice.get("delta", {})

    if is_first:
        events.append({
            "type": "response.created",
            "response": {
                "id": resp_id,
                "object": "response",
                "created_at": int(time.time()),
                "status": "in_progress",
                "model": chunk_json.get("model", ""),
                "output": []
            }
        })
        events.append({
            "type": "response.in_progress",
            "response": {
                "id": resp_id,
                "object": "response",
                "status": "in_progress"
            }
        })

    if delta.get("content"):
        if is_first:
            events.append({
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "message",
                    "id": f"msg_{uuid.uuid4().hex[:24]}",
                    "status": "in_progress",
                    "role": "assistant",
                    "content": []
                }
            })
            events.append({
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": ""}
            })
        events.append({
            "type": "response.output_text.delta",
            "output_index": 0,
            "content_index": 0,
            "delta": delta["content"]
        })

    if delta.get("tool_calls"):
        for tc in delta["tool_calls"]:
            fn = tc.get("function", {})
            tc_id = tc.get("id", f"call_{uuid.uuid4().hex[:24]}")
            if fn.get("name"):
                events.append({
                    "type": "response.output_item.added",
                    "output_index": len(delta.get("tool_calls", [])),
                    "item": {
                        "type": "function_call",
                        "id": f"fc_{uuid.uuid4().hex[:24]}",
                        "call_id": tc_id,
                        "name": fn.get("name", ""),
                        "arguments": "",
                        "status": "in_progress"
                    }
                })
            if fn.get("arguments"):
                events.append({
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "call_id": tc_id,
                    "delta": fn.get("arguments", "")
                })

    finish = choice.get("finish_reason")
    if finish:
        status = "completed"
        incomplete_reason = None
        if finish == "length":
            incomplete_reason = "max_tokens"

        events.append({
            "type": "response.completed",
            "response": {
                "id": resp_id,
                "object": "response",
                "created_at": int(time.time()),
                "status": status,
                "model": chunk_json.get("model", ""),
                "output": [],
                "usage": {}
            }
        })

    return events
