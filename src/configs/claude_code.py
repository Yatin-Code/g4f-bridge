import os
from ..bridge.utils import _get_claude_code_config_dir, _safe_read_json, _safe_write_json


def write_config(final_models, top_n=None):
    cc_dir = _get_claude_code_config_dir()
    settings_path = os.path.join(cc_dir, "settings.json")
    existing, err = _safe_read_json(settings_path)
    if err:
        print(f"  Could not read existing settings: {err}")
    existing["env"] = existing.get("env", {})
    existing["env"]["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:1337"
    existing["env"].pop("ANTHROPIC_API_KEY", None)
    existing["env"]["ANTHROPIC_AUTH_TOKEN"] = "dummy_key"
    existing["env"]["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    existing["env"]["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    existing["env"]["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] = "1"
    existing["env"].pop("CLAUDE_CODE_USE_BEDROCK", None)
    existing["env"].pop("CLAUDE_CODE_USE_VERTEX", None)
    existing["env"].pop("CLAUDE_CODE_USE_FOUNDRY", None)
    existing["forceLoginMethod"] = "console"
    ok, err = _safe_write_json(settings_path, existing)
    if ok:
        print(f"Claude Code: {settings_path} successfully updated!")
        print(f"  {len(final_models)} models via bridge. Disabled experimental betas & adaptive thinking.")
    else:
        print(f"Claude Code: Failed to write config: {err}")
