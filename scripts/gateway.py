#!/usr/bin/env python3
"""
Telegram Gateway for Omnibot (lightweight — raw HTTP, no heavy SDK).

Uses httpx + asyncio to call the Telegram Bot API directly.
Modeled after PicoClaw: long-polling, access control, streaming edits.

Usage:
    python scripts/gateway.py
    python scripts/gateway.py --config /path/to/config.json

Env vars:
    TG_BOT_TOKEN        (required)
    TG_ALLOW_FROM       (comma-separated user IDs, required)
    TG_PROXY            (optional, e.g. socks5://host:port)
    TG_STREAMING        (default: true)
    TG_USE_MARKDOWN_V2  (default: false)
    TG_DEBUG            (default: false)
"""

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants & logging
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_DIR / "config.json"
DEFAULT_MAX_LEN = 4096
POLL_TIMEOUT = 30          # long-poll seconds
STREAM_EDIT_INTERVAL = 0.8 # seconds between progressive edits
TYPING_INTERVAL = 4.5      # seconds between typing indicators

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tg-gateway")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(path: Path | None = None) -> dict:
    """Load config from file, then overlay env vars."""
    cfg: dict[str, Any] = {}
    config_path = path or DEFAULT_CONFIG
    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)

    # Env overrides
    if os.getenv("TG_BOT_TOKEN"):
        cfg["bot_token"] = os.getenv("TG_BOT_TOKEN")
    if os.getenv("TG_ALLOW_FROM"):
        raw = os.getenv("TG_ALLOW_FROM", "")
        cfg["allow_from"] = [x.strip() for x in raw.split(",") if x.strip()]
    if os.getenv("TG_PROXY"):
        cfg["proxy"] = os.getenv("TG_PROXY")
    if os.getenv("TG_STREAMING"):
        cfg["streaming"] = os.getenv("TG_STREAMING", "").lower() in ("true", "1", "y")
    if os.getenv("TG_USE_MARKDOWN_V2"):
        cfg["use_markdown_v2"] = os.getenv("TG_USE_MARKDOWN_V2", "").lower() in ("true", "1")
    if os.getenv("TG_DEBUG"):
        cfg["debug"] = os.getenv("TG_DEBUG", "").lower() in ("true", "1")

    # Defaults
    cfg.setdefault("bot_token", "")
    cfg.setdefault("allow_from", [])
    cfg.setdefault("streaming", True)
    cfg.setdefault("use_markdown_v2", False)
    cfg.setdefault("proxy", "")
    cfg.setdefault("debug", False)
    cfg.setdefault("max_message_length", DEFAULT_MAX_LEN)
    cfg.setdefault("typing_indicator", True)
    cfg.setdefault("base_url", "https://api.telegram.org")

    return cfg


# ---------------------------------------------------------------------------
# Telegram Bot API (raw HTTP)
# ---------------------------------------------------------------------------

class TelegramAPI:
    """Thin wrapper around the Telegram Bot HTTP API."""

    def __init__(self, token: str, base_url: str = "https://api.telegram.org", proxy: str = ""):
        self.base = f"{base_url}/bot{token}"
        self._client: httpx.AsyncClient | None = None
        self._proxy = proxy

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                proxy=self._proxy or None,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def call(self, method: str, **params) -> dict:
        """Call a Bot API method. Returns the 'result' field."""
        client = await self._get_client()
        url = f"{self.base}/{method}"
        # Filter out None values
        payload = {k: v for k, v in params.items() if v is not None}
        resp = await client.post(url, json=payload)
        data = resp.json()
        if not data.get("ok"):
            error_code = data.get("error_code", "?")
            description = data.get("description", "unknown")
            raise RuntimeError(f"Telegram API error {error_code}: {description}")
        return data.get("result")

    # Convenience methods
    async def get_updates(self, offset: int | None = None, timeout: int = POLL_TIMEOUT) -> list[dict]:
        return await self.call("getUpdates", offset=offset, timeout=timeout)

    async def send_message(self, chat_id: int | str, text: str, parse_mode: str | None = None,
                           reply_to: int | None = None, disable_preview: bool = True) -> dict:
        return await self.call(
            "sendMessage",
            chat_id=chat_id, text=text,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to,
            disable_web_page_preview=disable_preview,
        )

    async def edit_message(self, chat_id: int | str, message_id: int, text: str,
                           parse_mode: str | None = None, disable_preview: bool = True) -> dict:
        return await self.call(
            "editMessageText",
            chat_id=chat_id, message_id=message_id, text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_preview,
        )

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> dict:
        return await self.call("sendChatAction", chat_id=chat_id, action=action)

    async def get_me(self) -> dict:
        return await self.call("getMe")


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------

def split_message(text: str, max_len: int = DEFAULT_MAX_LEN) -> list[str]:
    """Split a long message into chunks ≤ max_len, preferring newline breaks."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to split at a newline
        split_at = text.rfind("\n", 0, max_len)
        if split_at <= 0:
            # Try space
            split_at = text.rfind(" ", 0, max_len)
        if split_at <= 0:
            # Hard cut
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Bridge to Omnibot agent (LLM-backed with conversation history)
# ---------------------------------------------------------------------------

class OmnibotBridge:
    """
    Bridges Telegram ↔ Omnibot agent via OpenAI-compatible API.

    Maintains per-user conversation history for multi-turn context.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.api_url = cfg.get("llm_api_url") or os.getenv("OMNIBOT_API_URL", "https://api.apifreellm.com/v1/chat/completions")
        self.api_key = cfg.get("llm_api_key") or os.getenv("OMNIBOT_API_KEY", "")
        self.model = cfg.get("llm_model") or os.getenv("OMNIBOT_MODEL", "gpt-4o-mini")
        self.system_prompt = cfg.get("system_prompt", "You are Ominibot, a helpful AI assistant. Be concise and helpful. Reply in the same language the user writes in.")
        self.max_history = cfg.get("max_history", 20)
        # Per-user conversation history: {user_id: [{"role": ..., "content": ...}]}
        self._history: dict[int, list[dict]] = {}

    def _get_history(self, user_id: int) -> list[dict]:
        if user_id not in self._history:
            self._history[user_id] = []
        return self._history[user_id]

    def _trim_history(self, user_id: int):
        h = self._get_history(user_id)
        if len(h) > self.max_history:
            self._history[user_id] = h[-self.max_history:]

    def clear_history(self, user_id: int):
        self._history.pop(user_id, None)

    async def process_message(self, user_text: str, user_id: int, chat_id: int,
                              username: str = "") -> str:
        """Process a message, maintain history, call LLM, return response."""
        history = self._get_history(user_id)
        history.append({"role": "user", "content": user_text})

        messages = [{"role": "system", "content": self.system_prompt}] + history

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                payload = {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 2048,
                    "temperature": 0.7,
                }

                resp = await client.post(self.api_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                choices = data.get("choices", [])
                if choices:
                    reply = choices[0].get("message", {}).get("content", "").strip()
                else:
                    reply = "(No response from agent)"

                # Store assistant reply in history
                history.append({"role": "assistant", "content": reply})
                self._trim_history(user_id)

                return reply

        except httpx.ConnectError as e:
            log.error(f"Bridge connection error: {e}")
            history.pop()  # Remove failed user message
            return "⚠️ Could not connect to LLM API. Check network."
        except httpx.HTTPStatusError as e:
            log.error(f"Bridge HTTP error: {e.response.status_code}")
            history.pop()
            return f"⚠️ LLM API error: {e.response.status_code}"
        except Exception as e:
            log.error(f"Bridge error: {e}")
            history.pop()
            return f"⚠️ Agent error: {e}"

    async def on_startup(self):
        log.info(f"Bridge init → model={self.model}, api={self.api_url}")

    async def on_shutdown(self):
        self._history.clear()


# Telegram Gateway
# ---------------------------------------------------------------------------

class TelegramGateway:
    """
    Bidirectional Telegram Bot Gateway.
    Long-polls Telegram, routes messages to OmnibotBridge, sends responses.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.api = TelegramAPI(
            token=cfg["bot_token"],
            base_url=cfg.get("base_url", "https://api.telegram.org"),
            proxy=cfg.get("proxy", ""),
        )
        self.bridge = OmnibotBridge(cfg)
        self.allow_from: set[int] = set()
        self.running = False
        self._offset: int | None = None
        self._tasks: list[asyncio.Task] = []

        # Parse allowed user IDs
        for uid in cfg.get("allow_from", []):
            try:
                self.allow_from.add(int(uid))
            except (ValueError, TypeError):
                log.warning(f"Invalid user ID in allow_from: {uid}")

        # Formatting
        self._parse_mode: str | None = None
        if cfg.get("use_markdown_v2"):
            self._parse_mode = "MarkdownV2"
        elif cfg.get("use_markdown"):
            self._parse_mode = "Markdown"

    def _is_allowed(self, user_id: int) -> bool:
        if not self.allow_from:
            return True  # No whitelist = allow all
        return user_id in self.allow_from

    @staticmethod
    def _user_display(update: dict) -> str:
        user = update.get("from", {})
        name = user.get("first_name", "")
        if user.get("last_name"):
            name += f" {user['last_name']}"
        return name or user.get("username", str(user.get("id", "?")))

    # ---- Command handlers ----

    async def _cmd_start(self, msg: dict):
        uid = msg["from"]["id"]
        chat_id = msg["chat"]["id"]
        name = self._user_display(msg)
        log.info(f"/start from {name} (id:{uid})")
        await self.api.send_message(
            chat_id,
            f"👋 Hello {name}!\n\n"
            f"I'm Omnibot's Telegram Gateway.\n"
            f"Send me a message and I'll route it to the agent.\n\n"
            f"Commands:\n"
            f"/start — This message\n"
            f"/status — Gateway status\n"
            f"/help — Help",
        )

    async def _cmd_status(self, msg: dict):
        chat_id = msg["chat"]["id"]
        uptime = time.time() - self._start_time if hasattr(self, "_start_time") else 0
        h, m = divmod(int(uptime), 3600)
        m, s = divmod(m, 60)
        status_text = (
            f"🟢 Gateway running\n"
            f"Uptime: {h}h {m}m {s}s\n"
            f"Streaming: {'on' if self.cfg.get('streaming') else 'off'}\n"
            f"Format: {self._parse_mode or 'HTML'}\n"
            f"Allow list: {len(self.allow_from)} users"
        )
        await self.api.send_message(chat_id, status_text)

    async def _cmd_help(self, msg: dict):
        chat_id = msg["chat"]["id"]
        await self.api.send_message(
            chat_id,
            "📖 **Omnibot Telegram Gateway**\n\n"
            "Just send me any message and I'll process it through the agent.\n\n"
            "**Commands:**\n"
            "/start — Welcome message\n"
            "/status — Gateway status\n"
            "/help — This help\n\n"
            "**Supported:**\n"
            "• Text messages\n"
            "• Photos & documents (descriptions)\n"
            "• Voice messages (transcription if available)\n"
            "• Long messages (auto-split)",
        )

    # ---- Message handlers ----

    async def _handle_text(self, msg: dict):
        uid = msg["from"]["id"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")
        username = msg["from"].get("username", "")
        msg_id = msg.get("message_id")

        # Check access
        if not self._is_allowed(uid):
            log.info(f"Blocked message from user {uid} (not in allow_from)")
            await self.api.send_message(chat_id, "⛔ Access denied.")
            return

        # Commands
        if text.startswith("/"):
            cmd = text.split()[0].lower()
            if cmd == "/start":
                await self._cmd_start(msg)
                return
            elif cmd == "/status":
                await self._cmd_status(msg)
                return
            elif cmd == "/help":
                await self._cmd_help(msg)
                return

        # Route to agent
        name = self._user_display(msg)
        log.info(f"Message from {name} (id:{uid}): {text[:80]}...")

        # Typing indicator
        typing_task = None
        if self.cfg.get("typing_indicator", True):
            typing_task = asyncio.create_task(self._typing_loop(chat_id))

        try:
            response = await self.bridge.process_message(text, uid, chat_id, username)
        finally:
            if typing_task:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

        # Send response
        if not response:
            await self.api.send_message(chat_id, "(empty response)")
            return

        # Streaming or batch send
        if self.cfg.get("streaming", True) and len(response) > 100:
            await self._send_streaming(chat_id, msg_id, response)
        else:
            await self._send_chunked(chat_id, response, reply_to=msg_id)

    async def _handle_photo(self, msg: dict):
        uid = msg["from"]["id"]
        chat_id = msg["chat"]["id"]
        if not self._is_allowed(uid):
            return
        caption = msg.get("caption", "(photo without caption)")
        name = self._user_display(msg)
        log.info(f"Photo from {name}: {caption[:80]}")
        response = await self.bridge.process_message(f"[Photo] {caption}", uid, chat_id,
                                                     msg["from"].get("username", ""))
        await self._send_chunked(chat_id, response, reply_to=msg.get("message_id"))

    async def _handle_document(self, msg: dict):
        uid = msg["from"]["id"]
        chat_id = msg["chat"]["id"]
        if not self._is_allowed(uid):
            return
        doc = msg.get("document", {})
        name = self._user_display(msg)
        desc = f"[Document: {doc.get('file_name', 'unknown')}]"
        log.info(f"Document from {name}: {desc}")
        response = await self.bridge.process_message(desc, uid, chat_id,
                                                     msg["from"].get("username", ""))
        await self._send_chunked(chat_id, response, reply_to=msg.get("message_id"))

    async def _handle_voice(self, msg: dict):
        uid = msg["from"]["id"]
        chat_id = msg["chat"]["id"]
        if not self._is_allowed(uid):
            return
        log.info(f"Voice from {self._user_display(msg)}")
        # TODO: integrate with STT if available
        await self.api.send_message(chat_id, "🎤 Voice received. Speech-to-text not yet configured.",
                                    reply_to=msg.get("message_id"))

    # ---- Response sending ----

    def _format_text(self, text: str) -> str:
        """Escape text for the selected parse mode."""
        if not self._parse_mode:
            # HTML mode: escape <>&
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if self._parse_mode == "MarkdownV2":
            # Escape MarkdownV2 special chars
            specials = r"_*[]()~`>#+-=|{}.!"
            out = []
            for ch in text:
                if ch in specials:
                    out.append(f"\\{ch}")
                else:
                    out.append(ch)
            return "".join(out)
        return text  # Markdown v1 — mostly safe

    async def _send_chunked(self, chat_id: int | str, text: str, reply_to: int | None = None):
        """Send a message, splitting if too long."""
        chunks = split_message(text, self.cfg.get("max_message_length", DEFAULT_MAX_LEN))
        for i, chunk in enumerate(chunks):
            try:
                formatted = self._format_text(chunk)
                await self.api.send_message(
                    chat_id, formatted,
                    parse_mode=self._parse_mode,
                    reply_to=reply_to if i == 0 else None,
                )
            except Exception as e:
                log.error(f"Failed to send chunk {i}: {e}")
                # Fallback: send without formatting
                try:
                    await self.api.send_message(chat_id, chunk, reply_to=reply_to if i == 0 else None)
                except Exception as e2:
                    log.error(f"Fallback send also failed: {e2}")

    async def _send_streaming(self, chat_id: int | str, reply_to: int | None, text: str):
        """Send response with progressive message edits (streaming)."""
        chunks = split_message(text, self.cfg.get("max_message_length", DEFAULT_MAX_LEN))

        # Send initial placeholder
        try:
            placeholder = await self.api.send_message(
                chat_id, "⏳ Thinking...",
                parse_mode=self._parse_mode,
                reply_to=reply_to,
            )
            placeholder_id = placeholder.get("message_id")
        except Exception:
            # If placeholder fails, fall back to chunked
            await self._send_chunked(chat_id, text, reply_to=reply_to)
            return

        # Progressive edits
        last_edit_time = 0.0
        for i, chunk in enumerate(chunks):
            now = time.time()
            # Rate-limit edits
            if now - last_edit_time < STREAM_EDIT_INTERVAL:
                await asyncio.sleep(STREAM_EDIT_INTERVAL - (now - last_edit_time))

            try:
                formatted = self._format_text(chunk)
                display = formatted if i == len(chunks) - 1 else formatted + "\n\n▶️..."
                await self.api.edit_message(
                    chat_id, placeholder_id, display,
                    parse_mode=self._parse_mode,
                )
                last_edit_time = time.time()
            except Exception as e:
                log.warning(f"Edit failed (chunk {i}): {e}")

    # ---- Typing indicator loop ----

    async def _typing_loop(self, chat_id: int | str):
        """Periodically send typing indicator while processing."""
        try:
            while True:
                try:
                    await self.api.send_chat_action(chat_id, "typing")
                except Exception:
                    pass
                await asyncio.sleep(TYPING_INTERVAL)
        except asyncio.CancelledError:
            pass

    # ---- Main loop ----

    async def start(self):
        """Start the long-polling gateway."""
        self._start_time = time.time()
        self.running = True

        # Verify bot connection
        try:
            me = await self.api.get_me()
            log.info(f"✅ Bot connected: @{me.get('username')} ({me.get('first_name')})")
            log.info(f"   ID: {me.get('id')}")
            log.info(f"   Groups: {me.get('can_join_groups')}")
        except Exception as e:
            log.error(f"❌ Failed to connect: {e}")
            return

        log.info(f"📡 Starting long-polling (timeout={POLL_TIMEOUT}s)...")
        log.info(f"   Streaming: {self.cfg.get('streaming')}")
        log.info(f"   Allow list: {self.allow_from or 'ALL'}")

        # Reset Telegram bot state — delete webhook clears pending polls
        try:
            await self.api.call("deleteWebhook", drop_pending_updates=True)
            log.info("   Reset: webhook cleared, pending updates dropped")
            log.info("   Waiting 35s for stale polls to fully expire...")
            await asyncio.sleep(35)
            log.info("   ...wait complete, starting polling now")
        except Exception:
            pass

        # Start bridge
        await self.bridge.on_startup()

        # Main polling loop
        try:
            while self.running:
                try:
                    updates = await self.api.get_updates(offset=self._offset, timeout=POLL_TIMEOUT)
                    for update in updates:
                        self._offset = update["update_id"] + 1
                        self._process_update(update)
                except httpx.ReadTimeout:
                    # Normal timeout — just loop
                    continue
                except RuntimeError as e:
                    if "409" in str(e) and "Conflict" in str(e):
                        log.warning("409 Conflict — another instance is polling. Resetting...")
                        self._offset = None
                        await asyncio.sleep(35)
                        continue
                    log.error(f"Poll error: {e}")
                    await asyncio.sleep(5)
                except Exception as e:
                    log.error(f"Poll error: {e}")
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            log.info("Gateway shutting down...")
        finally:
            await self.bridge.on_shutdown()
            await self.api.close()
            log.info("Gateway stopped.")

    def _process_update(self, update: dict):
        """Route an incoming update to the appropriate handler."""
        msg = update.get("message")
        if not msg:
            return

        # Route by content type
        if "text" in msg:
            asyncio.create_task(self._handle_text(msg))
        elif "photo" in msg:
            asyncio.create_task(self._handle_photo(msg))
        elif "document" in msg:
            asyncio.create_task(self._handle_document(msg))
        elif "voice" in msg or "audio" in msg:
            asyncio.create_task(self._handle_voice(msg))
        else:
            log.debug(f"Unhandled update type: {list(msg.keys())}")

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Omnibot Telegram Gateway")
    parser.add_argument("--config", type=Path, help="Path to config.json")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if not cfg.get("bot_token"):
        log.error("❌ No bot token! Set TG_BOT_TOKEN env var or add to config.json")
        sys.exit(1)

    if cfg.get("debug"):
        logging.getLogger().setLevel(logging.DEBUG)

    gateway = TelegramGateway(cfg)

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, gateway.stop)

    log.info("🚀 Starting Telegram Gateway...")
    await gateway.start()


if __name__ == "__main__":
    asyncio.run(main())
