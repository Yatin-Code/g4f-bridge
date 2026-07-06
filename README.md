# OpenCode G4F & EAON Bridge

A powerful, smart, and fully automatic backend proxy bridge designed specifically to integrate [G4F](https://g4f.space) and [EAON](https://eaon.dev) proxy networks with the strict **OpenCode** AI IDE ecosystem.

## Features

- **Dynamic Dual-Network Routing**: Seamlessly search, combine, and use models from both G4F and EAON simultaneously.
- **Strict Stream Sanitization**: OpenCode's `ai-sdk` is extremely strict and crashes on poorly formatted SSE streams. This bridge automatically intercepts, validates, and fixes broken streams on the fly (e.g., removing illegal repeated `role` fields).
- **Auto-Config Generation**: Instantly builds the `opencode.json` configuration file, organizing models into beautifully structured UI catalogs (complete with pagination to bypass OpenCode's 15-model visual limit).
- **Live Model Testing (`-t`)**: AI Proxy networks are notorious for having dead or overloaded models. The built-in testing suite sends a massive ~15,000 token payload with tool calls to stress-test every model, ensuring your OpenCode menu only contains models that can actually handle real workloads. See [TEST_FAILURES.md](TEST_FAILURES.md) for details.
- **First-Run Setup Wizard**: Safely manages your API keys locally without hardcoding them in the script.
- **Cross-Platform**: Works on Linux, macOS, and Windows out of the box.

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Yatin-Code/opencode-g4f-bridge.git
   cd opencode-g4f-bridge
   ```

2. **Install requirements:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the Setup Wizard:**
   Simply run the bridge. On the very first run, it will securely prompt you for your API keys.
   ```bash
   python smart_bridge.py
   ```
   *Note: If you only have one key (e.g., only G4F), simply press ENTER to skip the EAON prompt. The bridge will automatically disable the missing network and run flawlessly with the one you provided.*

4. **Update keys later:**
   If you need to add or change an API key later (e.g., you got an EAON key after initial setup):
   ```bash
   python smart_bridge.py -s
   ```

## Usage

### 1. Extract Top Models (Recommended)
Automatically extract the Top 15 most popular models from G4F, and ALL models from EAON:
```bash
python smart_bridge.py -b
```
You can also specify a number. When you do, that limit applies to **both** backends:
```bash
python smart_bridge.py -b 5    # Top 5 from G4F + Top 5 from EAON
```

### 2. Search for Specific Models
Interactively search for models by name or provider:
```bash
python smart_bridge.py -m "claude"
```
Select the models you want (e.g., `1, 3`), and they will be injected directly into your OpenCode configuration.

### 3. Run Live Health Tests
Append the `-t` (or `--test`) flag to any command to force the bridge to stress-test models with a massive ~15,000 token payload before saving them:
```bash
python smart_bridge.py -b -t
python smart_bridge.py -b 5 -t
python smart_bridge.py -m "gpt" -t
```
*If a model returns a 413 Content Too Large, 502 Bad Gateway, or times out after 25 seconds, the bridge will silently drop it so it doesn't crash your OpenCode session. See [TEST_FAILURES.md](TEST_FAILURES.md) for a detailed explanation of why models fail.*

## How It Works Under the Hood

When OpenCode requests a generation:
1. The bridge intercepts the request at `http://127.0.0.1:1337/v1/chat/completions`.
2. It looks up the model's exact ID and backend provider (G4F or EAON) from its internal `MODEL_MAP`.
3. It proxies the request securely to the upstream network, injecting the correct Authorization headers.
4. It catches the Server-Sent Events (SSE) stream, parses every chunk, strips illegal data, injects missing required fields, and forwards the heavily sanitized stream back to OpenCode.

## Configuration File Locations

| File | Linux/macOS | Windows |
|---|---|---|
| API Keys | `~/.opencode-g4f-bridge/keys.json` | `C:\Users\<you>\.opencode-g4f-bridge\keys.json` |
| OpenCode Config | `~/.config/opencode/opencode.json` | `~\.config\opencode\opencode.json` |

## License
This project is licensed under the GPL-3.0 License. See the `LICENSE` file for details.
