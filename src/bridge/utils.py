import json, os, sys, socket, shutil, platform, logging, threading, time

logger = logging.getLogger("g4f-bridge")
request_logger = logging.getLogger("g4f-bridge.requests")

CONFIG_PATH = None
BACKENDS = {}

_ACTIVE_REQUESTS = 0
_REQUEST_LOCK = threading.Lock()
_MAX_CONCURRENT = 50

def setup_logging(level=logging.INFO):
    root = logging.getLogger("g4f-bridge")
    root.setLevel(logging.DEBUG)
    if not root.handlers:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        fmt = logging.Formatter('%(asctime)s %(levelname)-5s %(message)s', datefmt='%H:%M:%S')
        ch.setFormatter(fmt)
        root.addHandler(ch)
        uvicorn_logger = logging.getLogger("uvicorn")
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = False

def get_active_requests():
    with _REQUEST_LOCK:
        return _ACTIVE_REQUESTS

def increment_requests():
    global _ACTIVE_REQUESTS
    with _REQUEST_LOCK:
        _ACTIVE_REQUESTS += 1
        return _ACTIVE_REQUESTS

def decrement_requests():
    global _ACTIVE_REQUESTS
    with _REQUEST_LOCK:
        _ACTIVE_REQUESTS = max(0, _ACTIVE_REQUESTS - 1)
        return _ACTIVE_REQUESTS

def log_request_status():
    active = get_active_requests()
    logger.info(f"[CONCURRENT] {active}/{_MAX_CONCURRENT} requests in flight")

STRIP_PARAMS_BY_BACKEND = {
    "G4F": ["parallel_tool_calls"],
    "EAON": ["parallel_tool_calls"],
    "PA": ["parallel_tool_calls"],
}

ALL_TARGETS = ["opencode", "claude-code", "codex", "cursor", "antigravity"]
TARGET_CHOICES = ALL_TARGETS + ["all"]

INSTALL_COMMANDS = {
    "claude-code": {
        "name": "Claude Code",
        "cmd": "curl -fsSL https://claude.ai/install.sh | bash",
        "url": "https://claude.ai/code",
    },
    "codex": {
        "name": "Codex CLI",
        "cmd": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
        "url": "https://developers.openai.com/codex",
    },
    "cursor": {
        "name": "Cursor CLI",
        "cmd": "curl https://cursor.com/install -fsS | bash",
        "url": "https://cursor.sh",
    },
    "antigravity": {
        "name": "Antigravity CLI",
        "cmd": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
        "url": "https://antigravity.google",
    },
}

def _get_bridge_config_dir():
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "g4f-bridge")
    return os.path.join(os.path.expanduser("~"), ".g4f-bridge")

def _migrate_old_config():
    old_dir = os.path.join(os.path.expanduser("~"), ".opencode-g4f-bridge")
    new_dir = _get_bridge_config_dir()
    if os.path.exists(old_dir) and not os.path.exists(new_dir):
        try:
            os.makedirs(new_dir, exist_ok=True)
            old_keys = os.path.join(old_dir, "keys.json")
            if os.path.exists(old_keys):
                shutil.copy2(old_keys, os.path.join(new_dir, "keys.json"))
                logger.info(f"Migrated config from {old_dir} to {new_dir}")
        except Exception:
            pass

def _get_opencode_config_dir():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(xdg, "opencode")
    return os.path.join(os.path.expanduser("~"), ".config", "opencode")

def _get_codex_config_dir():
    return os.path.join(os.path.expanduser("~"), ".codex")

def _get_cursor_config_dir():
    return os.path.join(os.path.expanduser("~"), ".cursor")

def _get_claude_code_config_dir():
    return os.path.join(os.path.expanduser("~"), ".claude")

def _get_antigravity_config_dir():
    return os.path.join(os.path.expanduser("~"), ".gemini")

PROVIDER_DEFAULTS = {
    "G4F": {"url": "https://g4f.space/v1", "key": ""},
    "EAON": {"url": "https://api.eaon.dev/v1", "key": ""},
}

PA_CONFIG = {"url": "https://g4f.space", "providers_path": "/pa/providers"}

def load_or_prompt_keys(force_setup=False):
    global CONFIG_PATH, BACKENDS
    _migrate_old_config()
    config_dir = _get_bridge_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    CONFIG_PATH = os.path.join(config_dir, "keys.json")
    keys = {k: "" for k in PROVIDER_DEFAULTS}

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
                keys.update(saved)
        except Exception:
            pass

    keys = {k: v for k, v in keys.items() if k in PROVIDER_DEFAULTS}

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                existing = json.load(f)
            stale = {k for k in existing if k not in PROVIDER_DEFAULTS}
            if stale:
                with open(CONFIG_PATH, "w") as f:
                    json.dump(keys, f, indent=4)
                logger.info(f"Cleaned up stale keys: {', '.join(stale)}")
        except Exception:
            pass

    if force_setup:
        print("G4F Bridge - API Key Management\n")
        try:
            for provider, defaults in PROVIDER_DEFAULTS.items():
                current = keys.get(provider, "")
                display_url = defaults["url"]
                print(f"  [{provider}] URL: {display_url}")
                print(f"  [{provider}] Key: {'[SET]' if current else '[NOT SET]'}{' (' + current[:8] + '...)' if current else ''}")
                val = input(f"  Enter new key for {provider} (or ENTER to keep): ").strip()
                if val:
                    keys[provider] = val
                    changed = True
                else:
                    changed = False
                default_url = PROVIDER_DEFAULTS[provider]["url"]
                url = input(f"  Enter URL for {provider} (or ENTER for default '{default_url}'): ").strip()
                if url:
                    PROVIDER_DEFAULTS[provider]["url"] = url
                    changed = True
                if changed:
                    with open(CONFIG_PATH, "w") as f:
                        json.dump(keys, f, indent=4)
                    logger.info(f"  -> {provider} keys saved")
                print()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
    elif not any(keys.values()):
        logger.error("No API keys found. Run: g4f-bridge --keys")
        sys.exit(1)

    for provider, defaults in PROVIDER_DEFAULTS.items():
        key = keys.get(provider, "")
        url = PROVIDER_DEFAULTS[provider]["url"]
        if key:
            BACKENDS[provider] = {"url": url, "key": key}
            logger.info(f"Backend '{provider}' configured: {url}")
    if "G4F" in BACKENDS:
        BACKENDS["PA"] = {"url": PA_CONFIG["url"], "key": BACKENDS["G4F"]["key"]}
        logger.info(f"Backend 'PA' configured using G4F key: {PA_CONFIG['url']}")

    if not BACKENDS:
        logger.error("No API keys provided. The bridge cannot operate without at least one provider.")
        sys.exit(1)

def manage_keys():
    setup_logging(logging.WARNING)
    load_or_prompt_keys(force_setup=True)

def _check_port_in_use(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False

def _check_tool_installed(tool):
    cmd_map = {
        "claude-code": "claude",
        "codex": "codex",
        "cursor": "cursor-agent",
        "antigravity": "agy",
    }
    cmd = cmd_map.get(tool)
    if not cmd:
        return True, None
    path = shutil.which(cmd)
    if path:
        return True, path
    return False, cmd

def _detect_config_conflicts(target, final_models):
    warnings = []

    if target == "claude-code":
        cc_dir = _get_claude_code_config_dir()
        settings_path = os.path.join(cc_dir, "settings.json")
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f:
                    existing = json.load(f)
                env = existing.get("env", {})
                if env.get("ANTHROPIC_API_KEY") and env["ANTHROPIC_API_KEY"] != "dummy_key":
                    warnings.append(
                        f"Existing ANTHROPIC_API_KEY in {settings_path} will be overwritten.\n"
                        f"Old key: {env['ANTHROPIC_API_KEY'][:8]}..."
                    )
                if env.get("ANTHROPIC_AUTH_TOKEN"):
                    warnings.append(
                        f"Existing ANTHROPIC_AUTH_TOKEN conflicts with ANTHROPIC_API_KEY.\n"
                        f"The bridge will remove it."
                    )
                if env.get("CLAUDE_CODE_USE_BEDROCK") or env.get("CLAUDE_CODE_USE_VERTEX") or env.get("CLAUDE_CODE_USE_FOUNDRY"):
                    warnings.append(
                        f"A CLAUDE_CODE_USE_* provider variable is set in {settings_path}.\n"
                        f"This overrides ANTHROPIC_BASE_URL and will be removed."
                    )
            except (json.JSONDecodeError, Exception):
                warnings.append(f"Could not parse {settings_path} — will overwrite.")

    elif target == "codex":
        codex_dir = _get_codex_config_dir()
        config_path = os.path.join(codex_dir, "config.toml")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    content = f.read()
                if "model_provider" in content and "g4f-bridge" not in content:
                    warnings.append(
                        f"Existing {config_path} has a different model_provider.\n"
                        f"It will be overwritten."
                    )
                if "openai_base_url" in content:
                    warnings.append(
                        f"Existing openai_base_url in {config_path} will be overwritten."
                    )
            except Exception:
                warnings.append(f"Could not read {config_path} — will overwrite.")

        if not os.environ.get("BRIDGE_API_KEY"):
            warnings.append(
                f"BRIDGE_API_KEY env var is not set. Codex CLI needs it.\n"
                f"Run: export BRIDGE_API_KEY=dummy_key"
            )

    elif target == "cursor":
        cursor_dir = _get_cursor_config_dir()
        config_path = os.path.join(cursor_dir, "settings.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    existing = json.load(f)
                if existing.get("openaiApiBaseUrl") and not existing["openaiApiBaseUrl"].startswith(f"http://127.0.0.1:1337"):
                    warnings.append(
                        f"Existing openaiApiBaseUrl in {config_path} will be overwritten."
                    )
            except (json.JSONDecodeError, Exception):
                warnings.append(f"Could not parse {config_path} — will overwrite.")

    elif target == "antigravity":
        ag_dir = _get_antigravity_config_dir()
        config_path = os.path.join(ag_dir, "settings.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    existing = json.load(f)
                if existing.get("baseUrl") and not existing["baseUrl"].startswith(f"http://127.0.0.1:1337"):
                    warnings.append(
                        f"Existing baseUrl in {config_path} will be overwritten."
                    )
            except (json.JSONDecodeError, Exception):
                warnings.append(f"Could not parse {config_path} — will overwrite.")

    return warnings

def _run_preflight_checks(targets):
    for target in targets:
        if target not in INSTALL_COMMANDS:
            continue
        installed, cmd = _check_tool_installed(target)
        if not installed:
            info = INSTALL_COMMANDS[target]
            logger.warning(f"{info['name']} ('{cmd}') not found in PATH.")
            logger.info(f"Install from: {info['url']}")
            logger.info(f"Install command: {info['cmd']}")
            choice = input("Press [y] to install now, or [Enter] to skip: ").strip().lower()
            if choice == 'y':
                print(f"\nInstalling {info['name']}...\n")
                import subprocess
                try:
                    proc = subprocess.Popen(
                        info['cmd'], shell=True,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True
                    )
                    for line in proc.stdout:
                        print(f"  {line}", end="")
                    proc.wait()
                    if proc.returncode != 0:
                        logger.error(f"Install failed (exit code {proc.returncode}).")
                        logger.info(f"Run manually: {info['cmd']}")
                        sys.exit(1)
                    installed_now, _ = _check_tool_installed(target)
                    if not installed_now:
                        logger.error(f"Install succeeded but '{cmd}' not found in PATH.")
                        logger.info(f"Restart your shell or add ~/.local/bin to PATH:")
                        logger.info(f"  export PATH=\"$HOME/.local/bin:$PATH\"")
                        sys.exit(1)
                    logger.info(f"{info['name']} installed successfully!\n")
                except subprocess.TimeoutExpired:
                    logger.error("Install timed out.")
                    sys.exit(1)
                except Exception as e:
                    logger.error(f"Install failed: {e}")
                    sys.exit(1)
            else:
                logger.info(f"Skipping. Run manually: {info['cmd']}")
                sys.exit(1)

    if _check_port_in_use(1337):
        logger.error(f"Port 1337 is already in use!")
        logger.info(f"Stop it: lsof -i :1337  (then kill the PID)")
        sys.exit(1)

def _safe_write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True, None
    except PermissionError:
        return False, f"Permission denied writing to {path}"
    except Exception as e:
        return False, str(e)

def _safe_write_text(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(data)
        return True, None
    except PermissionError:
        return False, f"Permission denied writing to {path}"
    except Exception as e:
        return False, str(e)

def _safe_read_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f), None
    except FileNotFoundError:
        return {}, None
    except json.JSONDecodeError as e:
        return {}, f"Could not parse JSON in {path}: {e}"
    except Exception as e:
        return {}, str(e)
