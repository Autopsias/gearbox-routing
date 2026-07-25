# Gemini 3 Flash + Claude Code Integration

> **Last Updated:** December 18, 2025
> **Status:** Working (with limitations - see Known Issues)

This document explains how to use Google Gemini 3 Flash with Claude Code via a local proxy server.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Configuration Files](#configuration-files)
5. [Usage](#usage)
6. [Known Issues](#known-issues)
7. [Alternative Approaches](#alternative-approaches)
8. [Troubleshooting](#troubleshooting)
9. [Updating the Setup](#updating-the-setup)

---

## Overview

### How It Works

```
gemini command
    │
    ├─► start-gemini.sh checks if proxy is running
    │      │
    │      ├─► If NOT running: starts claude-code-router in background
    │      └─► If running: skips (reuses existing proxy)
    │
    └─► Launches Claude Code with environment variables:
           • CLAUDE_CONFIG_DIR → ~/.claude-gemini
           • ANTHROPIC_BASE_URL → http://127.0.0.1:3456
           • ANTHROPIC_AUTH_TOKEN → gemini-proxy-token
           • ANTHROPIC_MODEL → gemini-3-flash-preview
```

### Architecture

| Component | Purpose |
|-----------|---------|
| **Claude Code Router** | Local proxy that translates Anthropic API format to Gemini API format |
| **Launcher Script** | Auto-starts proxy and launches Claude Code with correct environment |
| **Separate Config Dir** | Isolates Gemini settings from main Claude Code installation |

---

## Prerequisites

1. **Claude Code** installed and working
2. **Node.js** (for npm package installation)
3. **Google AI API Key** from [Google AI Studio](https://aistudio.google.com/)

---

## Installation

### Step 1: Install Claude Code Router

```bash
npm install -g @musistudio/claude-code-router
```

This installs the `ccr` command-line tool.

### Step 2: Create Configuration Directories

```bash
mkdir -p ~/.claude-code-router ~/.claude-gemini ~/.claude/scripts
```

### Step 3: Create Router Configuration

Create `~/.claude-code-router/config.json`:

```bash
cat > ~/.claude-code-router/config.json << 'EOF'
{
  "LOG": false,
  "LOG_LEVEL": "error",
  "HOST": "127.0.0.1",
  "PORT": 3456,
  "API_TIMEOUT_MS": 600000,
  "Providers": [
    {
      "name": "gemini",
      "api_base_url": "https://generativelanguage.googleapis.com/v1beta/models/",
      "api_key": "YOUR_GOOGLE_API_KEY_HERE",
      "models": ["gemini-3-flash-preview"],
      "transformer": {
        "use": ["gemini"]
      }
    }
  ],
  "Router": {
    "default": "gemini,gemini-3-flash-preview",
    "background": "gemini,gemini-3-flash-preview",
    "think": "gemini,gemini-3-flash-preview",
    "long_context": "gemini,gemini-3-flash-preview"
  }
}
EOF
```

**Replace `YOUR_GOOGLE_API_KEY_HERE` with your actual API key.**

### Step 4: Create Claude Code Settings for Gemini

Create `~/.claude-gemini/settings.json`:

```bash
cat > ~/.claude-gemini/settings.json << 'EOF'
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "env": {
    "ANTHROPIC_MODEL": "gemini-3-flash-preview"
  },
  "permissions": {
    "defaultMode": "bypassPermissions"
  }
}
EOF
```

### Step 5: Create Launcher Script

Create `~/.claude/scripts/start-gemini.sh`:

```bash
cat > ~/.claude/scripts/start-gemini.sh << 'EOF'
#!/bin/bash
# Auto-start Gemini proxy if not running, then launch Claude Code

# Check if router is already running
if ! ccr status 2>/dev/null | grep -q "Running"; then
    ccr start > /dev/null 2>&1
    sleep 2  # Wait for router to start
fi

# Launch Claude Code with Gemini config
# ANTHROPIC_AUTH_TOKEN bypasses login dialog (router handles actual auth)
CLAUDE_CONFIG_DIR="$HOME/.claude-gemini" \
ANTHROPIC_BASE_URL="http://127.0.0.1:3456" \
ANTHROPIC_AUTH_TOKEN="gemini-proxy-token" \
ANTHROPIC_MODEL="gemini-3-flash-preview" \
claude "$@"
EOF

chmod +x ~/.claude/scripts/start-gemini.sh
```

### Step 6: Add Shell Alias

Add to your `~/.zshrc` (or `~/.bashrc`):

```bash
# Google Gemini 3 Flash - single command, auto-starts proxy
alias gemini='~/.claude/scripts/start-gemini.sh'
```

### Step 7: Reload Shell and Test

```bash
source ~/.zshrc
gemini
```

---

## Configuration Files

### File Locations

| File | Purpose |
|------|---------|
| `~/.claude-code-router/config.json` | Router configuration (API key, model, ports) |
| `~/.claude-gemini/settings.json` | Claude Code settings for Gemini mode |
| `~/.claude/scripts/start-gemini.sh` | Launcher script with auto-start |
| `~/.zshrc` | Shell alias definition |

### Router Config Reference

```json
{
  "LOG": false,                    // Enable/disable logging
  "LOG_LEVEL": "error",            // Log level: debug, info, warn, error
  "HOST": "127.0.0.1",             // Local host binding
  "PORT": 3456,                    // Port for proxy server
  "API_TIMEOUT_MS": 600000,        // 10 minute timeout for long requests
  "Providers": [{
    "name": "gemini",              // Provider identifier
    "api_base_url": "...",         // Gemini API endpoint
    "api_key": "...",              // Your Google API key
    "models": ["gemini-3-flash-preview"],  // Available models
    "transformer": {
      "use": ["gemini"]            // Use Gemini transformer for API translation
    }
  }],
  "Router": {
    "default": "gemini,gemini-3-flash-preview",     // Default model
    "background": "gemini,gemini-3-flash-preview",  // Background tasks
    "think": "gemini,gemini-3-flash-preview",       // Extended thinking
    "long_context": "gemini,gemini-3-flash-preview" // Long context windows
  }
}
```

### Environment Variables

| Variable | Purpose | Value |
|----------|---------|-------|
| `CLAUDE_CONFIG_DIR` | Separate config to avoid auth conflicts | `~/.claude-gemini` |
| `ANTHROPIC_BASE_URL` | Points to local proxy instead of Anthropic | `http://127.0.0.1:3456` |
| `ANTHROPIC_AUTH_TOKEN` | Bypasses login dialog (any non-empty value) | `gemini-proxy-token` |
| `ANTHROPIC_MODEL` | Specifies the model to use | `gemini-3-flash-preview` |

---

## Usage

### Start Claude Code with Gemini

```bash
gemini
```

This single command:
1. Checks if the proxy is running
2. Starts the proxy if needed
3. Launches Claude Code connected to Gemini 3 Flash

### Verify Connection

Once inside Claude Code, ask:
```
What model are you?
```

The response should indicate Gemini 3 Flash.

### Stop the Proxy (Optional)

```bash
ccr stop
```

The proxy continues running after you exit Claude Code, making subsequent launches instant.

### Check Proxy Status

```bash
ccr status
```

---

## Known Issues

### Tool Calling Limitations

**Gemini 3 Flash has known issues with function/tool calling** that affect:

| Feature | Status | Impact |
|---------|--------|--------|
| Basic chat | Working | Full functionality |
| MCP tools | Not working | External tools don't function |
| Slash commands | Partial | Simple ones work, tool-based don't |
| Subagents | Not working | Task delegation fails |
| File editing | Not working | Uses tool calls internally |
| Code execution | Not working | Uses tool calls internally |

**Root Cause:** Gemini 3 requires `thought_signature` in function calls that the current router implementation doesn't properly handle.

**Workaround:** Use for chat/reasoning tasks only, or see Alternative Approaches below.

---

## Alternative Approaches

### Option 1: PAL MCP Server (Recommended for Full Features)

**PAL MCP Server** adds Gemini/GPT as MCP tools within Claude Code while keeping all Claude features working.

- **GitHub:** https://github.com/paulrobello/par_mcp
- **Approach:** Claude orchestrates, calls Gemini for specific tasks
- **Pros:** All Claude Code features work (MCP, slash commands, subagents)
- **Cons:** Gemini is a tool, not the main model

### Option 2: Gemini CLI

Google's official CLI with native MCP support.

- **GitHub:** https://github.com/google-gemini/gemini-cli
- **Approach:** Standalone tool, separate from Claude Code
- **Pros:** Native MCP support, full Gemini features
- **Cons:** Separate tool, not integrated with Claude Code

### Option 3: Use Gemini 2.5 Flash

The older model has working tool calling support.

Update router config to use `gemini-2.5-flash-preview` instead of `gemini-3-flash-preview`.

---

## Troubleshooting

### "Cannot connect to model"

1. Check if proxy is running:
   ```bash
   ccr status
   ```

2. Start proxy manually:
   ```bash
   ccr start
   ```

3. Check logs:
   ```bash
   ccr logs
   ```

### Login Dialog Appears

Ensure `ANTHROPIC_AUTH_TOKEN` is set in the launcher script (any non-empty value works).

### Wrong Model Being Used

1. Verify `~/.claude-gemini/settings.json` has correct model
2. Verify `ANTHROPIC_MODEL` environment variable in launcher script
3. Check router config has matching model in `Router.default`

### Port Already in Use

Change `PORT` in `~/.claude-code-router/config.json` and update `ANTHROPIC_BASE_URL` in launcher script.

### API Key Invalid

1. Verify key at [Google AI Studio](https://aistudio.google.com/)
2. Check key is correctly set in `~/.claude-code-router/config.json`
3. Ensure no extra spaces or quotes around the key

---

## Updating the Setup

### Change API Key

Edit `~/.claude-code-router/config.json` and update the `api_key` field.

### Change Model

Update these files:
1. `~/.claude-code-router/config.json` - `models` array and all `Router.*` entries
2. `~/.claude-gemini/settings.json` - `ANTHROPIC_MODEL` value
3. `~/.claude/scripts/start-gemini.sh` - `ANTHROPIC_MODEL` environment variable

### Update Claude Code Router

```bash
npm update -g @musistudio/claude-code-router
```

### Check for Gemini 3 Tool Support Fixes

Monitor these resources for updates:
- [Claude Code Router Issues](https://github.com/musistudio/claude-code-router/issues)
- [Google AI Forum](https://discuss.ai.google.dev/)
- [Gemini API Release Notes](https://ai.google.dev/gemini-api/docs/release-notes)

---

## Comparison with GLM Setup

| Aspect | GLM 4.6 | Gemini 3 Flash |
|--------|---------|----------------|
| Command | `glm` | `gemini` |
| Proxy | Z.ai (hosted, instant) | Claude Code Router (local) |
| First launch | Instant | ~2 sec (proxy startup) |
| Subsequent | Instant | Instant (proxy reused) |
| Tool calling | Works | Limited (known issue) |
| MCP support | Works | Not working |

---

## Quick Reference

### Commands

```bash
gemini              # Start Claude Code with Gemini
ccr status          # Check proxy status
ccr start           # Start proxy manually
ccr stop            # Stop proxy
ccr logs            # View proxy logs
```

### File Locations

```
~/.claude-code-router/config.json    # Router config
~/.claude-gemini/settings.json       # Claude Code settings
~/.claude/scripts/start-gemini.sh    # Launcher script
~/.zshrc                             # Shell alias
```

### Current Configuration

- **Model:** `gemini-3-flash-preview`
- **Proxy Port:** `3456`
- **Config Directory:** `~/.claude-gemini`

---

## References

- [Claude Code Router](https://github.com/musistudio/claude-code-router)
- [Google AI Studio](https://aistudio.google.com/)
- [Gemini API Docs](https://ai.google.dev/gemini-api/docs)
- [PAL MCP Server](https://github.com/paulrobello/par_mcp)
- [Gemini CLI](https://github.com/google-gemini/gemini-cli)
