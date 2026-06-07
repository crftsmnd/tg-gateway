# 🤖 tg-gateway

**Lightweight bidirectional Telegram Bot Gateway** — raw HTTP, no heavy SDK, instant startup.

[![PyPI](https://img.shields.io/pypi/v/tg-gateway-bot)](https://pypi.org/project/tg-gateway-bot/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)

Turn any OpenAI-compatible LLM into a Telegram bot in seconds.

```
pip install tg-gateway-bot
tg-gateway-bot
```

## Features

- **Long-polling** — no public IP, no webhook, no ngrok
- **Bidirectional** — users message the bot → LLM processes → replies stream back
- **Streaming** — progressively edits messages as the agent generates text
- **Access control** — whitelist of allowed Telegram user IDs
- **Multi-turn history** — per-user conversation context (up to 20 messages)
- **Message splitting** — auto-splits responses > 4096 chars
- **Typing indicator** — shows "typing..." while processing
- **Photo & document support** — sends captions to the LLM
- **Proxy support** — HTTP/SOCKS5
- **Graceful shutdown** — handles SIGTERM/SIGINT cleanly

## Quick Start

### 1. Install

```bash
pip install tg-gateway-bot
```

### 2. Create a Telegram Bot

Talk to [@BotFather](https://t.me/botfather) on Telegram and create a new bot. Save the token.

### 3. Configure

```bash
tg-gateway-bot
```

It'll look for `config.json` in the current directory. Create one:

```json
{
  "bot_token": "123456:ABCdef...",
  "allow_from": ["your_telegram_user_id"],
  "llm_api_url": "https://api.openai.com/v1/chat/completions",
  "llm_api_key": "sk-...",
  "llm_model": "gpt-4o-mini"
}
```

> **How to get your Telegram user ID?** Message [@userinfobot](https://t.me/userinfobot) on Telegram.

### 4. Run

```bash
tg-gateway-bot
```

Or with a custom config path:

```bash
tg-gateway-bot --config /path/to/config.json
```

### 5. Message Your Bot

Open Telegram, find your bot, and send it a message! 🎉

## Installation Alternatives

### From source

```bash
git clone https://github.com/crftsmnd/tg-gateway.git
cd tg-gateway
cp config.example.json config.json
# edit config.json with your tokens
pip install httpx[socks]
python scripts/gateway.py
```

### Via CLI tool

```bash
pip install tg-gateway-bot
tgctl      # same as tg-gateway-bot
```

## Environment Variables

All config fields can be overridden via environment variables:

| Variable | Description |
|---|---|
| `TG_BOT_TOKEN` | Telegram Bot API token |
| `TG_ALLOW_FROM` | Comma-separated allowed user IDs |
| `TG_PROXY` | Proxy URL (e.g. `socks5://host:port`) |
| `TG_STREAMING` | Enable/disable streaming (`true`/`false`) |
| `TG_USE_MARKDOWN_V2` | Use MarkdownV2 formatting |
| `TG_DEBUG` | Enable debug logging |

## Configuration

| Field | Default | Description |
|---|---|---|
| `base_url` | `https://api.telegram.org` | Custom Bot API URL (self-hosted) |
| `streaming` | `true` | Progressive message editing |
| `typing_indicator` | `true` | Show typing while processing |
| `max_message_length` | `4096` | Max chars per message |
| `use_markdown_v2` | `false` | MarkdownV2 parse mode |
| `system_prompt` | *(see config example)* | LLM system prompt |
| `max_history` | `20` | Multi-turn context window |

## Deployment

For 24/7 operation, run on a VPS or Raspberry Pi:

```bash
nohup tg-gateway-bot > gateway.log 2>&1 &
```

Or use `systemd` / `supervisor` / `screen` / `tmux`.

## Python API

```python
import asyncio
from tg_gateway import TelegramGateway, load_config

async def main():
    cfg = load_config("config.json")
    gateway = TelegramGateway(cfg)
    await gateway.start()

asyncio.run(main())
```

## Architecture

```
Telegram User → Bot API → Long Poll → TelegramGateway
                                          ↓
                                    OmnibotBridge
                                          ↓
                                  LLM API (OpenAI, etc.)
                                          ↓
                                    Response → Telegram
```

## License

MIT
