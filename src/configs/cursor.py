from ..bridge.utils import _get_cursor_config_dir, _safe_read_json, _safe_write_json


def write_config(final_models, top_n=None):
    cursor_dir = _get_cursor_config_dir()
    cursor_config_path = os.path.join(cursor_dir, "settings.json")
    existing, err = _safe_read_json(cursor_config_path)
    if err:
        print(f"  Could not read existing settings: {err}")
    first_model = final_models[0]["label"] if final_models else ""
    existing["openaiApiBaseUrl"] = "http://127.0.0.1:1337/v1"
    existing["openaiApiKey"] = "dummy_key"
    ok, err = _safe_write_json(cursor_config_path, existing)
    if ok:
        print(f"Cursor: {cursor_config_path} successfully updated!")
        print(f"  In Cursor: Settings > Models > OpenAI API > set base URL to http://127.0.0.1:1337/v1")
        print(f"  {len(final_models)} models available. Select model: {first_model}")
    else:
        print(f"Cursor: Failed to write config: {err}")
