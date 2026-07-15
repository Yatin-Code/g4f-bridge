import os
from ..bridge.utils import _get_opencode_config_dir, _safe_write_json


def get_display_name(m, top_n=None):
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


def _build_config(final_models, top_n=None):
    config = {"provider": {}}

    g4f_models = {m["label"]: {"name": get_display_name(m, top_n)} for m in final_models if m["backend"] == "G4F"}
    if g4f_models:
        for i, chunk in enumerate(chunk_models(g4f_models, 15)):
            name = "G4F" if len(g4f_models) <= 15 else f"G4F (Page {i+1})"
            config["provider"][f"g4f-exact-bridge-{i}"] = {
                "npm": "@ai-sdk/openai-compatible",
                "name": name,
                "options": {
                    "baseURL": "http://127.0.0.1:1337/v1",
                    "apiKey": "dummy_key"
                },
                "models": chunk
            }

    eaon_models = {m["label"]: {"name": get_display_name(m, top_n)} for m in final_models if m["backend"] == "EAON"}
    if eaon_models:
        for i, chunk in enumerate(chunk_models(eaon_models, 15)):
            name = "EAON" if len(eaon_models) <= 15 else f"EAON (Page {i+1})"
            config["provider"][f"eaon-bridge-{i}"] = {
                "npm": "@ai-sdk/openai-compatible",
                "name": name,
                "options": {
                    "baseURL": "http://127.0.0.1:1337/v1",
                    "apiKey": "dummy_key"
                },
                "models": chunk
            }

    return config


def write_config(final_models, top_n=None):
    config_dir = _get_opencode_config_dir()
    config_path = os.path.join(config_dir, "opencode.json")
    config = _build_config(final_models, top_n)
    ok, err = _safe_write_json(config_path, config)
    if ok:
        print(f"OpenCode: {config_path} successfully updated!")
    else:
        print(f"OpenCode: Failed to write config: {err}")
