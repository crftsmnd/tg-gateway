#!/usr/bin/env python3
"""
tgctl — CLI management tool for the Telegram Gateway (lightweight).

Usage:
    python tgctl.py setup       # Interactive setup (create config)
    python tgctl.py start       # Start gateway in background
    python tgctl.py stop        # Stop gateway
    python tgctl.py restart     # Restart gateway
    python tgctl.py status      # Check gateway status
    python tgctl.py logs        # Tail gateway logs
    python tgctl.py test        # Test bot connection
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PID_FILE = Path("/tmp/tg-gateway.pid")
LOG_FILE = Path("/tmp/tg-gateway.log")
CONFIG_FILE = SKILL_DIR / "config.json"
GATEWAY_SCRIPT = SKILL_DIR / "scripts" / "gateway.py"


def _running():
    """Return PID if gateway is alive, else None."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return None


def setup():
    """Interactive setup wizard."""
    print("🔧 Telegram Gateway Setup")
    print("=" * 40)

    cfg = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        print(f"Loaded existing config from {CONFIG_FILE}")

    token = input(f"\nBot token [{cfg.get('bot_token', '')}]: ").strip()
    if token:
        cfg["bot_token"] = token
    if not cfg.get("bot_token"):
        print("❌ Bot token is required!")
        sys.exit(1)

    allow = input(f"Allowed user IDs (comma-separated) [{','.join(cfg.get('allow_from', []))}]: ").strip()
    if allow:
        cfg["allow_from"] = [x.strip() for x in allow.split(",") if x.strip()]

    streaming = input(f"Enable streaming edits? [{'Y' if cfg.get('streaming', True) else 'n'}]: ").strip()
    if streaming:
        cfg["streaming"] = streaming.lower() in ("y", "yes", "true", "1")

    md_v2 = input(f"Use MarkdownV2? [{'Y' if cfg.get('use_markdown_v2', False) else 'n'}]: ").strip()
    if md_v2:
        cfg["use_markdown_v2"] = md_v2.lower() in ("y", "yes", "true", "1")

    proxy = input(f"Proxy URL (optional) [{cfg.get('proxy', '')}]: ").strip()
    if proxy:
        cfg["proxy"] = proxy

    debug = input(f"Debug mode? [{'Y' if cfg.get('debug', False) else 'n'}]: ").strip()
    if debug:
        cfg["debug"] = debug.lower() in ("y", "yes", "true", "1")

    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

    print(f"\n✅ Config saved to {CONFIG_FILE}")
    print("   python tgctl.py test    # Test bot connection")
    print("   python tgctl.py start   # Start gateway")


def start():
    """Start gateway in background."""
    pid = _running()
    if pid:
        print(f"⚠️  Already running (PID {pid})")
        return

    if not CONFIG_FILE.exists():
        print("❌ No config.json. Run 'python tgctl.py setup' first.")
        sys.exit(1)

    print("🚀 Starting Telegram Gateway...")

    with open(LOG_FILE, "w") as log:
        proc = subprocess.Popen(
            [sys.executable, str(GATEWAY_SCRIPT)],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(SKILL_DIR),
        )

    PID_FILE.write_text(str(proc.pid))
    time.sleep(2)

    if proc.poll() is not None:
        print(f"❌ Failed. Check: cat {LOG_FILE}")
        PID_FILE.unlink(missing_ok=True)
        sys.exit(1)

    print(f"✅ Started (PID {proc.pid})")
    print(f"   Logs: tail -f {LOG_FILE}")


def stop():
    """Stop the gateway."""
    pid = _running()
    if not pid:
        print("ℹ️  Not running.")
        return

    print(f"⏹️  Stopping (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass

    PID_FILE.unlink(missing_ok=True)
    print("✅ Stopped.")


def restart():
    stop()
    time.sleep(1)
    start()


def status():
    """Check gateway status."""
    pid = _running()
    if pid:
        print(f"✅ Running (PID {pid})")
        if LOG_FILE.exists():
            lines = LOG_FILE.read_text().strip().split("\n")
            print("\n📋 Recent logs:")
            for line in lines[-10:]:
                print(f"   {line}")
    else:
        print("❌ Not running.")


def logs():
    """Tail gateway logs."""
    if not LOG_FILE.exists():
        print("No log file found.")
        return
    print(f"📋 Tailing {LOG_FILE} (Ctrl+C to stop)...")
    try:
        subprocess.run(["tail", "-f", str(LOG_FILE)])
    except KeyboardInterrupt:
        pass


def test():
    """Test bot connection via Telegram Bot API."""
    if not CONFIG_FILE.exists():
        print("❌ No config.json. Run 'python tgctl.py setup' first.")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        cfg = json.load(f)

    token = cfg.get("bot_token", "")
    if not token:
        print("❌ No bot token in config.")
        sys.exit(1)

    print("🔍 Testing bot connection...")
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        req = urllib.request.Request(url, headers={"User-Agent": "Omnibot-TG-Gateway/0.2"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())

        if data.get("ok"):
            bot = data["result"]
            print(f"✅ Bot connected!")
            print(f"   Name:     {bot.get('first_name', 'N/A')}")
            print(f"   Username: @{bot.get('username', 'N/A')}")
            print(f"   ID:       {bot.get('id', 'N/A')}")
        else:
            print(f"❌ API error: {data}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()
    commands = {
        "setup": setup, "start": start, "stop": stop,
        "restart": restart, "status": status, "logs": logs, "test": test,
    }

    if cmd in commands:
        commands[cmd]()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
