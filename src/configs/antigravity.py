from ..bridge.utils import _get_antigravity_config_dir, _safe_read_json, _safe_write_json


def write_config(final_models, top_n=None):
    ag_dir = _get_antigravity_config_dir()
    ag_config_path = os.path.join(ag_dir, "settings.json")
    existing, err = _safe_read_json(ag_config_path)
    if err:
        print(f"  Could not read existing settings: {err}")
    first_model = final_models[0]["label"] if final_models else ""
    existing["baseUrl"] = "http://127.0.0.1:1337/v1"
    ok, err = _safe_write_json(ag_config_path, existing)
    if ok:
        print(f"Antigravity: {ag_config_path} successfully updated!")
        print(f"  {len(final_models)} models available. Select model: {first_model}")
        print(f"  If the above doesn't work, set: export GEMINI_API_BASE_URL=http://127.0.0.1:1337/v1")
    else:
        print(f"Antigravity: Failed to write config: {err}")
        print(f"  Set env var: export GEMINI_API_BASE_URL=http://127.0.0.1:1337/v1")
