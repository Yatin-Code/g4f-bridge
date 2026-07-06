import json
import requests
import traceback
import uvicorn
import time
import argparse
import sys
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import platform

# ==========================================
# 1. CONFIGURATION & BACKENDS
# ==========================================
PORT = 1337

def _get_bridge_config_dir():
    """Get the bridge's own config directory (for API keys), cross-platform."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "opencode-g4f-bridge")
    return os.path.join(os.path.expanduser("~"), ".opencode-g4f-bridge")

def _get_opencode_config_dir():
    """Get OpenCode's config directory, cross-platform."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "opencode")
    return os.path.join(os.path.expanduser("~"), ".config", "opencode")

CONFIG_PATH = os.path.join(_get_bridge_config_dir(), "keys.json")

BACKENDS = {}

def load_or_prompt_keys(force_setup=False):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    keys = {"G4F": "", "EAON": ""}
    
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
                keys.update(saved)
        except Exception:
            pass
            
    needs_save = False
    
    if force_setup or (not keys.get("G4F") and not keys.get("EAON") and not os.path.exists(CONFIG_PATH)):
        print("🚀 Welcome to OpenCode G4F Bridge Setup!")
        if force_setup:
            print(f"Current G4F Key: {'[SET]' if keys.get('G4F') else '[NOT SET]'}")
            print(f"Current EAON Key: {'[SET]' if keys.get('EAON') else '[NOT SET]'}")
            print("Press ENTER to keep existing key, or type a new one.\n")
        else:
            print("Please enter your API keys below. Press ENTER to skip a provider if you don't use it.\n")
        
        g4f = input("Enter your G4F API Key: ").strip()
        if g4f:
            keys["G4F"] = g4f
            needs_save = True
            
        eaon = input("Enter your EAON API Key: ").strip()
        if eaon:
            keys["EAON"] = eaon
            needs_save = True
            
        if force_setup and not needs_save:
            print("No keys updated.")
            
    if needs_save:
        with open(CONFIG_PATH, "w") as f:
            json.dump(keys, f, indent=4)
        print(f"✅ Keys securely saved to {CONFIG_PATH}\n")
        
    if keys.get("G4F"):
        BACKENDS["G4F"] = {"url": "https://g4f.space/v1", "key": keys["G4F"]}
    if keys.get("EAON"):
        BACKENDS["EAON"] = {"url": "https://api.eaon.dev/v1", "key": keys["EAON"]}
        
    if not BACKENDS:
        print("❌ CRITICAL ERROR: No API keys provided. The bridge cannot operate without at least one provider.")
        sys.exit(1)

# We wait to run setup until after argparse is parsed
# so we can check for the --setup flag.

# Map of { label : model_object }
# model_object = {"id": str, "label": str, "model": str, "requests": int, "backend": str}
MODEL_MAP = {}

def get_all_models():
    all_models = []
    
    # 1. Fetch from G4F
    if "G4F" in BACKENDS:
        print(f"Fetching models from {BACKENDS['G4F']['url']}/models ...")
        try:
            resp = requests.get(f"{BACKENDS['G4F']['url']}/models", headers={"Authorization": f"Bearer {BACKENDS['G4F']['key']}"})
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for m in data:
                if m.get("id") == "auto": continue
                all_models.append({
                    "id": m.get("id"),
                    "label": m.get("label", m.get("id")),
                    "model": m.get("model", ""),
                    "requests": m.get("requests", 0),
                    "backend": "G4F"
                })
        except Exception as e:
            print(f"❌ Failed to fetch G4F models: {e}")
        
    # 2. Fetch from EAON
    if "EAON" in BACKENDS:
        print(f"Fetching models from {BACKENDS['EAON']['url']}/models ...")
        try:
            resp = requests.get(f"{BACKENDS['EAON']['url']}/models", headers={"Authorization": f"Bearer {BACKENDS['EAON']['key']}"})
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for m in data:
                if m.get("id") == "auto": continue
                # EAON models just have an 'id', we format the label for UI clarity
                model_id = m.get("id")
                all_models.append({
                    "id": model_id,
                    "label": f"EAON:{model_id}",
                    "model": model_id,
                    "requests": 0, # EAON doesn't expose usage metrics
                    "backend": "EAON"
                })
        except Exception as e:
            print(f"❌ Failed to fetch EAON models: {e}")
        
    # Sort all combined models by requests (descending)
    all_models = sorted(all_models, key=lambda x: x["requests"], reverse=True)
    return all_models

def test_model_live(model_obj):
    label = model_obj["label"]
    model_id = model_obj["id"]
    backend = model_obj["backend"]
    
    print(f"  🧪 Testing model '{label}' via {backend} backend...")
    
    # OpenCode typically sends massive context payloads (10k-30k+ tokens). 
    # Models with tiny TPM/context limits (like some Groq proxies with 8k limits) 
    # will pass a basic ping but crash OpenCode with a 413 Content Too Large error. 
    # We simulate this by sending a large dummy payload (~15,000 tokens).
    large_context = "This is a dummy context string to test large context windows. " * 1500
    
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": f"You are a test agent. {large_context}"},
            {"role": "user", "content": "say hi"}
        ],
        "tools": [{
            "type": "function", 
            "function": {
                "name": "test_tool", 
                "description": "A test tool to verify compatibility.",
                "parameters": {"type": "object", "properties": {}}
            }
        }],
        "stream": False
    }
    
    headers = {
        "Authorization": f"Bearer {BACKENDS[backend]['key']}",
        "Content-Type": "application/json"
    }
    
    try:
        resp = requests.post(f"{BACKENDS[backend]['url']}/chat/completions", json=payload, headers=headers, timeout=25)
        if resp.status_code == 200:
            print(f"    ✅ Success!")
            return True
        else:
            print(f"    ❌ Failed with {resp.status_code}: {resp.text[:100]}")
            print(f"       -> Reason: Model does not meet OpenCode's strict payload/tool requirements.")
            print(f"       -> Learn more: https://github.com/Yatin-Code/opencode-g4f-bridge/blob/main/TEST_FAILURES.md")
            return False
    except requests.exceptions.Timeout:
        print(f"    ❌ Failed: Connection timed out after 25 seconds.")
        print(f"       -> Reason: The upstream proxy server is dead or overloaded.")
        print(f"       -> Learn more: https://github.com/Yatin-Code/opencode-g4f-bridge/blob/main/TEST_FAILURES.md")
        return False
    except Exception as e:
        print(f"    ❌ Failed with exception: {e}")
        print(f"       -> Learn more: https://github.com/Yatin-Code/opencode-g4f-bridge/blob/main/TEST_FAILURES.md")
        return False

def interactive_model_selection(search_term, all_models):
    matches = [m for m in all_models if search_term.lower() in m.get("label", "").lower()]
    if not matches:
        print(f"❌ No models found matching '{search_term}'.")
        return []
        
    print(f"\n🔍 Found {len(matches)} matching providers for '{search_term}':")
    for i, m in enumerate(matches, 1):
        reqs = f"{m['requests']} reqs" if m['requests'] > 0 else "Unknown usage"
        print(f"  {i}. {m['label']} ({reqs})")
    
    print(f"  A. All of them")
    print(f"  Q. Quit")
    
    while True:
        choice = input("\nSelect providers (comma-separated numbers, A, or Q): ").strip().lower()
        if choice == 'q':
            sys.exit(0)
        elif choice == 'a':
            return matches
        else:
            try:
                parts = [int(p.strip()) for p in choice.split(",") if p.strip()]
                if parts and all(1 <= idx <= len(matches) for idx in parts):
                    return [matches[idx-1] for idx in parts]
            except ValueError:
                pass
        print("Invalid choice, please try again.")

def generate_opencode_config(selected_models=None, do_test=False, top_n=None):
    all_models = get_all_models()
    if not all_models:
        print("⚠️ No models fetched. Cannot generate config.")
        return
        
    global MODEL_MAP
    MODEL_MAP.clear()
    
    # Map EVERYTHING so the bridge recognizes any valid model string
    for m in all_models:
        MODEL_MAP[m["label"]] = m
            
    pre_test_models = []
    
    if selected_models is not None:
        pre_test_models = selected_models
    else:
        if top_n is not None:
            if top_n == -1:
                g4f_top = [m for m in all_models if m["backend"] == "G4F"][:15]
                eaon_top = [m for m in all_models if m["backend"] == "EAON"]
                print(f"🌟 Selecting Top 15 models from G4F and ALL {len(eaon_top)} models from EAON.")
            else:
                g4f_top = [m for m in all_models if m["backend"] == "G4F"][:top_n]
                eaon_top = [m for m in all_models if m["backend"] == "EAON"][:top_n]
                print(f"🌟 Selecting Top {top_n} models from G4F and Top {top_n} models from EAON.")
            pre_test_models = g4f_top + eaon_top
        else:
            # Default to ALL models across both backends
            pre_test_models = all_models
            
    final_models = []
    if do_test:
        print("\n🔬 Running live tests on selected models...")
        for m in pre_test_models:
            if test_model_live(m):
                final_models.append(m)
        print(f"✅ {len(final_models)} out of {len(pre_test_models)} passed the test.")
    else:
        final_models = pre_test_models
        
    if not final_models:
        print("⚠️ No valid models to save to opencode.json. Exiting.")
        sys.exit(1)
        
    print(f"✅ Saving {len(final_models)} models to opencode.json...")
    
    config = {
        "provider": {}
    }
    
    def get_display_name(m):
        if m["backend"] == "G4F" and m["requests"] > 0 and top_n is not None:
            return f"{m['label']} ({m['requests']})"
        return m["label"]
    
    def chunk_models(models_dict, max_size=15):
        chunks = []
        current_chunk = {}
        for k, v in models_dict.items():
            current_chunk[k] = v
            if len(current_chunk) == max_size:
                chunks.append(current_chunk)
                current_chunk = {}
        if current_chunk:
            chunks.append(current_chunk)
        return chunks
    
    g4f_models = {m["label"]: {"name": get_display_name(m)} for m in final_models if m["backend"] == "G4F"}
    if g4f_models:
        for i, chunk in enumerate(chunk_models(g4f_models, 15)):
            name = "G4F" if len(g4f_models) <= 15 else f"G4F (Page {i+1})"
            config["provider"][f"g4f-exact-bridge-{i}"] = {
                "npm": "@ai-sdk/openai-compatible",
                "name": name,
                "options": {
                    "baseURL": f"http://127.0.0.1:{PORT}/v1",
                    "apiKey": "dummy_key" 
                },
                "models": chunk
            }
        
    eaon_models = {m["label"]: {"name": get_display_name(m)} for m in final_models if m["backend"] == "EAON"}
    if eaon_models:
        for i, chunk in enumerate(chunk_models(eaon_models, 15)):
            name = "EAON" if len(eaon_models) <= 15 else f"EAON (Page {i+1})"
            config["provider"][f"eaon-bridge-{i}"] = {
                "npm": "@ai-sdk/openai-compatible",
                "name": name,
                "options": {
                    "baseURL": f"http://127.0.0.1:{PORT}/v1",
                    "apiKey": "dummy_key"
                },
                "models": chunk
            }
    config_dir = _get_opencode_config_dir()
    config_path = os.path.join(config_dir, "opencode.json")
    os.makedirs(config_dir, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"✅ {config_path} successfully updated!")

# ==========================================
# 2. SERVER CONFIG & PROXY ROUTING
# ==========================================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    
    # OpenCode injects advanced parameters that many proxy networks (G4F/EAON) 
    # crash on. We strip them out silently to ensure compatibility.
    for key in ["reasoning_effort", "parallel_tool_calls"]:
        if key in payload:
            del payload[key]
            
    requested_label = payload.get("model")
    
    print(f"\n" + "="*50)
    print(f"📥 Incoming request for model: '{requested_label}'")
    
    if requested_label not in MODEL_MAP:
        print(f"❌ Rejected: Model '{requested_label}' is not recognized.")
        return JSONResponse(status_code=400, content={"error": f"Model '{requested_label}' not recognized."})
    
    model_obj = MODEL_MAP[requested_label]
    backend = model_obj["backend"]
    actual_model_id = model_obj["id"]
    backend_url = BACKENDS[backend]["url"]
    backend_key = BACKENDS[backend]["key"]
    
    print(f"🔄 Translating label to ID: '{actual_model_id}' via {backend} API")
    payload["model"] = actual_model_id
        
    print(f"🚀 Proxying request directly to {backend_url} ...")
    
    headers = {
        "Authorization": f"Bearer {backend_key}",
        "Content-Type": "application/json"
    }
    
    try:
        is_stream = payload.get("stream", False)
        
        if is_stream:
            upstream_req = requests.post(
                f"{backend_url}/chat/completions", 
                json=payload, 
                headers=headers, 
                stream=True,
                timeout=120
            )
            
            if upstream_req.status_code != 200:
                err_text = upstream_req.text
                print(f"❌ Upstream error ({upstream_req.status_code}): {err_text}")
                return JSONResponse(status_code=upstream_req.status_code, content={"error": err_text})
            
            def stream_generator():
                print("✅ Streaming response back to OpenCode (with strict validation)...")
                first_chunk_sent = False
                
                stream_id = f"chatcmpl-bridge-{int(time.time())}"
                stream_created = int(time.time())
                
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
                        yield b"data: [DONE]\n\n"
                        break
                        
                    try:
                        chunk_json = json.loads(data_str)
                        print(f"  [RAW] {data_str[:120]}...")
                        
                        if not chunk_json.get("choices") or len(chunk_json["choices"]) == 0:
                            print("    -> ⚠️ Skipping bad chunk: 'choices' array is empty")
                            continue
                            
                        choice = chunk_json["choices"][0]
                        if "delta" not in choice:
                            choice["delta"] = {}
                            
                        chunk_json["id"] = stream_id
                        chunk_json["created"] = stream_created
                        chunk_json["object"] = "chat.completion.chunk"
                        chunk_json["model"] = actual_model_id
                            
                        if not first_chunk_sent:
                            if "role" not in choice["delta"]:
                                print("    -> 🔧 Injecting 'role': 'assistant' into first chunk")
                                choice["delta"]["role"] = "assistant"
                            first_chunk_sent = True
                        else:
                            # CRITICAL FIX: EAON illegally sends 'role' on every chunk, which breaks OpenCode.
                            if "role" in choice["delta"]:
                                del choice["delta"]["role"]
                            
                        fixed_data_str = json.dumps(chunk_json)
                        print(f"  [FIX] {fixed_data_str[:120]}...")
                        
                        yield f"data: {fixed_data_str}\n\n".encode('utf-8')
                        
                    except json.JSONDecodeError:
                        print(f"  [ERR] Failed to parse JSON: {data_str}")
                        yield line + b"\n"
                    except Exception as e:
                        print(f"  [ERR] Exception during chunk processing: {e}")
                        yield line + b"\n"
                        
            return StreamingResponse(stream_generator(), media_type="text/event-stream")
            
        else:
            response = requests.post(f"{backend_url}/chat/completions", json=payload, headers=headers)
            if response.status_code != 200:
                print(f"❌ Upstream error ({response.status_code}): {response.text}")
                return JSONResponse(status_code=response.status_code, content={"error": response.text})
                
            print("✅ Response successfully retrieved!")
            return JSONResponse(content=response.json())
            
    except Exception as e:
        print(f"❌ CRITICAL ERROR during proxying:")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart G4F/EAON Bridge with OpenCode config generation")
    parser.add_argument("-m", "--model", type=str, help="Search for a specific model to use")
    parser.add_argument("-t", "--test", action="store_true", help="Test the selected models before adding them")
    parser.add_argument("-b", "--best", nargs='?', const=-1, default=None, type=int, help="Extract top N models from G4F (defaults to 15) and ALL models from EAON")
    parser.add_argument("-s", "--setup", action="store_true", help="Run the API key setup wizard to update keys")
    args = parser.parse_args()
    
    # Run setup wizard (either forced via flag, or automatically on first run)
    load_or_prompt_keys(force_setup=args.setup)
    
    if args.setup:
        # If user ONLY ran --setup, exit after updating keys
        if not args.model and args.best is None:
            sys.exit(0)
    
    if args.model:
        all_models = get_all_models()
        if not all_models:
            sys.exit(1)
        selected = interactive_model_selection(args.model, all_models)
        if not selected:
            print("No models selected. Exiting.")
            sys.exit(0)
        generate_opencode_config(selected_models=selected, do_test=args.test)
    else:
        generate_opencode_config(top_n=args.best, do_test=args.test)
        
    print(f"\n🚀 Starting Smart Bridge on http://127.0.0.1:{PORT}...")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
