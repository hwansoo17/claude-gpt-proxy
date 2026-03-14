"""
Codex API client via direct HTTP (OAuth).
Supports multi-account with round-robin and failover.
"""

import json
import os
import time
import threading
import requests

CODEX_API_URL = os.environ.get("CODEX_API_URL", "https://chatgpt.com/backend-api/codex/responses")
TOKEN_URL = os.environ.get("CODEX_TOKEN_URL", "https://auth.openai.com/oauth/token")
CLIENT_ID = os.environ.get("CODEX_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")

MAX_FAILURES = int(os.environ.get("CODEX_MAX_FAILURES", "3"))
COOLDOWN_SECONDS = float(os.environ.get("CODEX_COOLDOWN_SECONDS", "120"))


class _CodexAccount:
    """Single Codex OAuth account."""

    def __init__(self, label, access_token, refresh_token=None, auth_file=None):
        self.label = label
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.auth_file = auth_file
        self.expires_at = time.time() + 86400 * 10
        self.failure_count = 0
        self.disabled_until = 0

    @property
    def is_available(self):
        return time.time() >= self.disabled_until

    def mark_failure(self):
        self.failure_count += 1
        if self.failure_count >= MAX_FAILURES:
            self.disabled_until = time.time() + COOLDOWN_SECONDS
            print(f"[codex] account '{self.label}' disabled for {COOLDOWN_SECONDS}s after {self.failure_count} failures")

    def mark_success(self):
        self.failure_count = 0

    def refresh_if_needed(self):
        if not self.refresh_token:
            return
        if time.time() < self.expires_at - 300:
            return
        try:
            resp = requests.post(TOKEN_URL, data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": CLIENT_ID,
                "scope": "openid profile email offline_access",
            }, headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }, timeout=10)
            if resp.ok:
                tokens = resp.json()
                self.access_token = tokens["access_token"]
                if tokens.get("refresh_token"):
                    self.refresh_token = tokens["refresh_token"]
                self.expires_at = time.time() + tokens.get("expires_in", 86400)
                self._save_tokens()
                print(f"[codex] account '{self.label}' token refreshed")
            else:
                print(f"[codex] account '{self.label}' refresh failed: {resp.status_code}")
        except Exception as e:
            print(f"[codex] account '{self.label}' refresh error: {e}")

    def _save_tokens(self):
        if not self.auth_file:
            return
        try:
            with open(self.auth_file) as f:
                data = json.load(f)
            data["tokens"]["access_token"] = self.access_token
            data["tokens"]["refresh_token"] = self.refresh_token
            data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(self.auth_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def build_headers(self):
        self.refresh_if_needed()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "text/event-stream",
            "originator": "codex-cli",
            "User-Agent": "codex-cli/1.0.18 (macOS; arm64)",
            "session_id": f"{int(time.time() * 1000)}-{os.urandom(4).hex()}",
        }


class CodexClient:
    """Multi-account Codex client with round-robin and failover."""

    def __init__(self):
        self._accounts = []
        self._index = 0
        self._lock = threading.Lock()
        self._load_accounts()

    def _load_accounts(self):
        # 1. Multiple auth files (comma-separated)
        auth_files = os.environ.get("CODEX_AUTH_FILES", "")
        if auth_files:
            for i, path in enumerate(auth_files.split(",")):
                path = os.path.expanduser(path.strip())
                if not path:
                    continue
                acc = self._load_from_file(path, f"file-{i+1}")
                if acc:
                    self._accounts.append(acc)
            if self._accounts:
                print(f"[codex] {len(self._accounts)} accounts loaded from CODEX_AUTH_FILES")
                return

        # 2. Single auth file
        auth_file = os.path.expanduser(os.environ.get("CODEX_AUTH_FILE", "~/.codex/auth.json"))
        acc = self._load_from_file(auth_file, "default")
        if acc:
            self._accounts.append(acc)
            print("[codex] 1 account loaded")

    @staticmethod
    def _load_from_file(path, label):
        try:
            with open(path) as f:
                data = json.load(f)
            tokens = data.get("tokens", {})
            return _CodexAccount(
                label=label,
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token"),
                auth_file=path,
            )
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[codex] auth load error ({path}): {e}")
            return None

    def _next_account(self):
        with self._lock:
            n = len(self._accounts)
            if n == 0:
                return None
            for _ in range(n):
                acc = self._accounts[self._index % n]
                self._index += 1
                if acc.is_available:
                    return acc
            return self._accounts[0]

    def stream(self, body):
        acc = self._next_account()
        if not acc:
            yield "error", {"error": "No codex accounts configured"}
            return

        headers = acc.build_headers()
        resp = requests.post(CODEX_API_URL, headers=headers, json=body, stream=True, timeout=300)

        if not resp.ok:
            error_text = resp.text[:500]
            print(f"[codex] API error {resp.status_code} (account '{acc.label}'): {error_text}")
            acc.mark_failure()
            yield "error", {"error": error_text}
            return

        acc.mark_success()
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
                yield "chunk", event
            except json.JSONDecodeError:
                continue

    @property
    def account_count(self):
        return len(self._accounts)
