# 🤖 tg-gateway

**Lightweight bidirectional Telegram Bot Gateway** — raw HTTP, no heavy SDK, instant startup.

Turn any OpenAI-compatible LLM into a Telegram bot in seconds.

```
pip install httpx[socks]
python scripts/gateway.py
```

## Features

- **Long-polling** — no public IP, no webhook, no ngrok
- **Bidirectional** — users message the bot → LLM processes → replies stream back
- **Streaming** — progressively edits messages as the agent generates text (like ChatGPT)
- **Access control** — whitelist of allowed Telegram user IDs
- **Multi-turn history** — per-user conversation context (up to 20 messages)
- **Message splitting** — auto-splits responses > 4096 chars
- **Typing indicator** — shows "typing..." while processing
- **Photo & document support** — sends captions to the LLM
- **Proxy support** — HTTP/SOCKS5
- **Graceful shutdown** — handles SIGTERM/SIGINT cleanly

## Quick Start

### 1. Create a Telegram Bot

Talk to [@BotFather](https://t.me/botfather) on Telegram and create a new bot. Save the token.

### 2. Configure

```bash
cp config.example.json config.json
```

Edit `config.json`:

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

### 3. Run

```bash
pip install httpx[socks]
python scripts/gateway.py
```

Or via the management CLI:

```bash
python tgctl.py test      # Test bot connection
python tgctl.py start     # Start in background
python tgctl.py status    # Check status
python tgctl.py stop      # Stop gateway
```

### 4. Message Your Bot

Open Telegram, find your bot, and send it a message! 🎉

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
| `system_prompt` | *(see example)* | LLM system prompt |
| `max_history` | `20` | Multi-turn context window |

## Deployment

For 24/7 operation, run on a VPS or Raspberry Pi:

```bash
nohup python scripts/gateway.py > gateway.log 2>&1 &
```

Or use `systemd` / `supervisor` / `screen` / `tmux`.

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
