import json, sys, logging, requests
from .utils import BACKENDS

logger = logging.getLogger("g4f-bridge.models")

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
    # G4F family (g4f.space gateway + its PA providers): models are listed
    # per upstream server (e.g. "crowllm.com:glm-5.2"), which pins requests
    # to a server that may be down. Dedupe by the clean upstream name and
    # let the gateway auto-route to a working provider.
    g4f_by_name = {}

    if "G4F" in BACKENDS:
        logger.info(f"Fetching models from {BACKENDS['G4F']['url']}/models")
        try:
            resp = requests.get(
                f"{BACKENDS['G4F']['url']}/models",
                headers={"Authorization": f"Bearer {BACKENDS['G4F']['key']}"}
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for m in data:
                name = (m.get("model") or m.get("id") or "").strip()
                if not name or name == "auto":
                    continue
                existing = g4f_by_name.get(name)
                if existing is None or m.get("requests", 0) > existing.get("requests", 0):
                    g4f_by_name[name] = {
                        "id": name,
                        "label": name,
                        "model": name,
                        "requests": m.get("requests", 0),
                        "backend": "G4F"
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch G4F models: {e}")

    if "EAON" in BACKENDS:
        eaon_models = fetch_eaon_catalog()
        all_models.extend(eaon_models)
        before = len([m for m in all_models if m["backend"] == "EAON"])
        monitor_operational = fetch_eaon_monitor()
        if monitor_operational:
            all_models = [m for m in all_models if m["backend"] != "EAON" or m["id"] in monitor_operational]
            after = len([m for m in all_models if m["backend"] == "EAON"])
            if before > after:
                logger.info(f"  -> Filtered out {before - after} non-operational EAON models")

    if "EAON" in BACKENDS:
        all_models.extend(fetch_eaon_static_beta_models())

    if "AGENTROUTER" in BACKENDS:
        all_models.extend(fetch_agentrouter_models())

    if "PA" in BACKENDS:
        pa_url = BACKENDS["PA"]["url"].rstrip("/")
        logger.info(f"Fetching PA providers from {pa_url}/pa/providers")
        try:
            resp = requests.get(f"{pa_url}/pa/providers", timeout=15)
            if resp.status_code == 200:
                providers = resp.json()
                pa_new = 0
                for prov in providers:
                    for mid in prov.get("models", []):
                        name = (mid or "").strip()
                        if not name or name == "auto":
                            continue
                        if name not in g4f_by_name:
                            g4f_by_name[name] = {
                                "id": name,
                                "label": name,
                                "model": name,
                                "requests": 0,
                                "backend": "G4F"
                            }
                            pa_new += 1
                logger.info(f"  -> {len(g4f_by_name)} G4F models ({pa_new} unique from PA providers)")
            else:
                logger.warning(f"PA providers endpoint returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"Failed to fetch PA providers: {e}")

    all_models.extend(g4f_by_name.values())

    if "OMNIROUTE" in BACKENDS:
        all_models.extend(fetch_omniroute_models())

    if "RE" in BACKENDS:
        all_models.extend(fetch_re_models())

    all_models = sorted(all_models, key=lambda x: x["requests"], reverse=True)
    return all_models

def fetch_re_models():
    if "RE" not in BACKENDS:
        return []
    logger.info(f"Fetching RE models from {BACKENDS['RE']['url']}/models")
    try:
        resp = requests.get(
            f"{BACKENDS['RE']['url']}/models",
            headers={"Authorization": f"Bearer {BACKENDS['RE']['key']}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        result = []
        for m in data:
            model_id = m.get("id")
            if not model_id or model_id == "auto":
                continue
            result.append({
                "id": model_id,
                "label": f"RE:{model_id}",
                "model": model_id,
                "requests": 0,
                "backend": "RE",
                "owned_by": m.get("owned_by", ""),
            })
        logger.info(f"  -> {len(result)} RE models")
        return result
    except Exception as e:
        logger.warning(f"Failed to fetch RE models: {e}")
        return []

def fetch_omniroute_models():
    if "OMNIROUTE" not in BACKENDS:
        return []
    logger.info(f"Fetching OmniRoute models from {BACKENDS['OMNIROUTE']['url']}/models")
    try:
        resp = requests.get(
            f"{BACKENDS['OMNIROUTE']['url']}/models",
            headers={"Authorization": f"Bearer {BACKENDS['OMNIROUTE']['key']}"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        result = []
        for m in data:
            model_id = m.get("id")
            if not model_id or model_id == "auto":
                continue
            result.append({
                "id": model_id,
                "label": f"OMNIROUTE:{model_id}",
                "model": model_id,
                "requests": 0,
                "backend": "OMNIROUTE",
                "owned_by": m.get("owned_by", ""),
            })
        logger.info(f"  -> {len(result)} OmniRoute models")
        return result
    except Exception as e:
        logger.warning(f"Failed to fetch OmniRoute models: {e}")
        return []

def fetch_eaon_catalog():
    if "EAON" not in BACKENDS:
        return []
    logger.info("Fetching EAON model catalog")
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
        logger.info(f"  -> {len(result)} EAON models ({instant_count} instant, {plus_count} plus)")
        return result
    except Exception as e:
        logger.warning(f"Failed to fetch EAON catalog: {e}")
        return []

AGENTROUTER_STATIC_MODELS = [
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "gpt-5.6-sol",
    "gpt-5.5",
    "glm-5.2",
]

# EAON beta-tier models guaranteed available regardless of the dynamic
# catalog/monitor. usage_limit = how much a request counts against the
# beta quota (0 = free). None means no limit tracking.
EAON_STATIC_BETA_MODELS = [
    {"id": "deepseek-v4-flash", "usage_limit": 0.0},
    {"id": "mimo-v2.5", "usage_limit": 0.0},
    {"id": "hy3", "usage_limit": 1.0},
    {"id": "deepseek-v4-pro", "usage_limit": 1.1},
    {"id": "mimo-v2.5-pro", "usage_limit": 1.1},
    {"id": "qwen3.7-plus", "usage_limit": 1.25},
    {"id": "minimax-m2.7", "usage_limit": 1.65},
    {"id": "qwen3.6-plus", "usage_limit": 1.7},
    {"id": "minimax-m3", "usage_limit": 1.7},
    {"id": "gpt-5.6-luna", "usage_limit": 3.0},
    {"id": "kimi-k2.7-code", "usage_limit": 4.1},
    {"id": "kimi-k2.6", "usage_limit": 4.8},
    {"id": "glm-5.2", "usage_limit": 18.2},
    {"id": "qwen3.7-max", "usage_limit": 18.2},
    {"id": "grok-4.5", "usage_limit": 142.0},
    {"id": "qwen3.8-max", "usage_limit": 142.0},
    {"id": "kimi-k3", "usage_limit": None},
]

def fetch_eaon_static_beta_models():
    if "EAON" not in BACKENDS:
        return []
    return [
        {
            "id": entry["id"],
            "label": f"EAON:{entry['id']}",
            "model": entry["id"],
            "requests": 0,
            "backend": "EAON",
            "tier": "beta",
            "usage_limit": entry["usage_limit"],
        }
        for entry in EAON_STATIC_BETA_MODELS
    ]

def fetch_agentrouter_models():
    if "AGENTROUTER" not in BACKENDS:
        return []
    result = [
        {
            "id": model_id,
            "label": f"agentrouter:{model_id}",
            "model": model_id,
            "requests": 0,
            "backend": "AGENTROUTER",
        }
        for model_id in AGENTROUTER_STATIC_MODELS
    ]
    logger.info(f"  -> {len(result)} static AGENTROUTER models")
    return result

def fetch_eaon_monitor():
    global EAON_OPERATIONAL_MODELS
    if "EAON" not in BACKENDS:
        EAON_OPERATIONAL_MODELS = set()
        return set()
    logger.info("Checking EAON model health")
    try:
        resp = requests.get(
            f"{BACKENDS['EAON']['url']}/monitor/models",
            headers={"Authorization": f"Bearer {BACKENDS['EAON']['key']}"}
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        operational = {m.get("id") for m in data if m.get("status") == "operational"}
        EAON_OPERATIONAL_MODELS = operational
        logger.info(f"  -> {len(operational)} operational EAON models")
        unavailable = [m for m in data if m.get("status") != "operational"]
        if unavailable:
            for m in unavailable:
                logger.debug(f"     {m.get('id')}: {m.get('status')}")
        return operational
    except Exception as e:
        logger.warning(f"Failed to fetch EAON monitor: {e}")
        EAON_OPERATIONAL_MODELS = set()
        return set()

def test_model_live(model_obj):
    label = model_obj["label"]
    model_id = model_obj["id"]
    backend = model_obj["backend"]

    if backend == "EAON":
        if model_id in EAON_OPERATIONAL_MODELS:
            logger.info(f"  Model '{label}' is operational per EAON monitor — proceeding to stress test")
        elif EAON_OPERATIONAL_MODELS:
            logger.warning(f"  Model '{label}' is NOT operational per EAON monitor. Skipping.")
            return False
        else:
            logger.info(f"  No monitor data for '{label}' — proceeding directly to stress test")

    logger.info(f"  Testing model '{label}' via {backend} backend")

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
                logger.info(f"    Tool call confirmed — model supports function calling")
                return True
            elif saw_content:
                logger.warning(f"    Model streamed text but never called the tool")
                return False
            else:
                logger.warning(f"    Empty stream received")
                return False
        else:
            logger.warning(f"    Failed with {resp.status_code}: {resp.text[:100]}")
            return False
    except requests.exceptions.Timeout:
        logger.warning(f"    Failed: Connection timed out after 25 seconds.")
        return False
    except Exception as e:
        logger.warning(f"    Failed with exception: {e}")
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
        logger.warning(f"No models found matching '{terms}'.")
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
