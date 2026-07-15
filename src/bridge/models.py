import json
import sys
import requests
from .utils import BACKENDS

MODEL_MAP = {}
CLAUDE_MODEL_MAP = {}
EAON_OPERATIONAL_MODELS = set()
ACTIVE_MODELS = set()


def _resolve_model(requested_label):
    if requested_label in MODEL_MAP:
        return MODEL_MAP[requested_label]
    if requested_label in CLAUDE_MODEL_MAP:
        return CLAUDE_MODEL_MAP[requested_label]
    return None


def get_all_models():
    all_models = []

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
            print(f"Failed to fetch G4F models: {e}")

    if "EAON" in BACKENDS:
        eaon_models = fetch_eaon_catalog()
        all_models.extend(eaon_models)

    if "EAON" in BACKENDS:
        monitor_operational = fetch_eaon_monitor()
        if monitor_operational:
            before = len([m for m in all_models if m["backend"] == "EAON"])
            all_models = [m for m in all_models if m["backend"] != "EAON" or m["id"] in monitor_operational]
            after = len([m for m in all_models if m["backend"] == "EAON"])
            if before > after:
                print(f"  -> Filtered out {before - after} non-operational EAON models")

    all_models = sorted(all_models, key=lambda x: x["requests"], reverse=True)
    return all_models


def fetch_eaon_catalog():
    if "EAON" not in BACKENDS:
        return []
    print("Fetching EAON model catalog...")
    try:
        resp = requests.get(
            f"{BACKENDS['EAON']['url']}/models/catalog",
            headers={"Authorization": f"Bearer {BACKENDS['EAON']['key']}"}
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        result = []
        for m in data:
            model_id = m.get("id")
            tier = m.get("tier", "unknown")
            result.append({
                "id": model_id,
                "label": f"EAON:{model_id}",
                "model": model_id,
                "requests": 0,
                "backend": "EAON",
                "tier": tier
            })
        instant_count = len([m for m in result if m["tier"] == "instant"])
        plus_count = len([m for m in result if m["tier"] == "plus"])
        print(f"  -> {len(result)} EAON models found ({instant_count} instant, {plus_count} plus)")
        return result
    except Exception as e:
        print(f"Failed to fetch EAON catalog: {e}")
        return []


def fetch_eaon_monitor():
    global EAON_OPERATIONAL_MODELS
    if "EAON" not in BACKENDS:
        EAON_OPERATIONAL_MODELS = set()
        return set()
    print("Checking EAON model health...")
    try:
        resp = requests.get(
            f"{BACKENDS['EAON']['url']}/monitor/models",
            headers={"Authorization": f"Bearer {BACKENDS['EAON']['key']}"}
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        operational = {m.get("id") for m in data if m.get("status") == "operational"}
        EAON_OPERATIONAL_MODELS = operational
        print(f"  -> {len(operational)} operational EAON models")
        unavailable = [m for m in data if m.get("status") != "operational"]
        if unavailable:
            for m in unavailable:
                print(f"     {m.get('id')}: {m.get('status')}")
        return operational
    except Exception as e:
        print(f"Failed to fetch EAON monitor: {e}")
        EAON_OPERATIONAL_MODELS = set()
        return set()


def test_model_live(model_obj):
    label = model_obj["label"]
    model_id = model_obj["id"]
    backend = model_obj["backend"]

    if backend == "EAON":
        if model_id in EAON_OPERATIONAL_MODELS:
            print(f"  Model '{label}' is operational per EAON monitor — proceeding to stress test...")
        elif EAON_OPERATIONAL_MODELS:
            print(f"  Model '{label}' is NOT operational per EAON monitor. Skipping.")
            return False
        else:
            print(f"  No monitor data for '{label}' — proceeding directly to stress test...")

    print(f"  Testing model '{label}' via {backend} backend...")

    large_context = "This is a dummy context string to test large context windows. " * 1500

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": f"You are a test agent. {large_context}"},
            {"role": "user", "content": "Call the test_tool function right now to confirm tool support, then stop."}
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test tool to verify compatibility.",
                "parameters": {"type": "object", "properties": {}}
            }
        }],
        "stream": True
    }

    headers = {
        "Authorization": f"Bearer {BACKENDS[backend]['key']}",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(f"{BACKENDS[backend]['url']}/chat/completions", json=payload, headers=headers, stream=True, timeout=25)
        if resp.status_code == 200:
            saw_content = False
            saw_tool_call = False
            for line in resp.iter_lines():
                if not line:
                    continue
                decoded = line.decode('utf-8')
                if not decoded.startswith("data: "):
                    continue
                data_str = decoded[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if delta.get("content"):
                        saw_content = True
                    if delta.get("tool_calls"):
                        saw_tool_call = True
                        break
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
            resp.close()
            if saw_tool_call:
                print(f"    Tool call confirmed — model supports function calling")
                return True
            elif saw_content:
                print(f"    Model streamed text but never called the tool")
                return False
            else:
                print(f"    Empty stream received")
                return False
        else:
            print(f"    Failed with {resp.status_code}: {resp.text[:100]}")
            return False
    except requests.exceptions.Timeout:
        print(f"    Failed: Connection timed out after 25 seconds.")
        return False
    except Exception as e:
        print(f"    Failed with exception: {e}")
        return False


def interactive_model_selection(search_terms, all_models):
    seen = set()
    matches = []
    for term in search_terms:
        for m in all_models:
            label = m.get("label", "").lower()
            if term.lower() in label and label not in seen:
                seen.add(label)
                matches.append(m)
    if not matches:
        terms = "', '".join(search_terms)
        print(f"No models found matching '{terms}'.")
        return []

    terms = "', '".join(search_terms)
    print(f"\nFound {len(matches)} matching providers for '{terms}':")
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
