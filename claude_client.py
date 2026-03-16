"""
Claude API client via Claude Code OAuth.
Supports multi-account with round-robin load balancing and failover.
Loads tokens from: env var -> credentials file (Linux) -> macOS Keychain.
"""

import json
import os
import platform
import subprocess
import time
import uuid
import threading
import requests

ANTHROPIC_API_URL = os.environ.get("CLAUDE_API_URL", "https://api.anthropic.com/v1/messages")
ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_CODE_VERSION = os.environ.get("CLAUDE_CODE_VERSION", "2.1.76")
OAUTH_TOKEN_URL = os.environ.get("CLAUDE_OAUTH_TOKEN_URL", "https://console.anthropic.com/api/oauth/token")
OAUTH_CLIENT_ID = os.environ.get("CLAUDE_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e")
KEYCHAIN_SERVICE = os.environ.get("CLAUDE_KEYCHAIN_SERVICE", "Claude Code-credentials")
CREDENTIALS_FILE = os.path.expanduser(os.environ.get("CLAUDE_CREDENTIALS_FILE", "~/.claude/.credentials.json"))

BETAS_BASE = [
    "claude-code-20250219",
    "oauth-2025-04-20",
    "interleaved-thinking-2025-05-14",
    "redact-thinking-2026-02-12",
    "context-management-2025-06-27",
    "prompt-caching-scope-2026-01-05",
]

MODEL_CONFIG = {
    "claude-sonnet-4-6": {
        "betas": BETAS_BASE + ["effort-2025-11-24"],
        "thinking": {"type": "adaptive"},
        "max_tokens": 64000,
        "output_config": {"effort": "high"},
    },
    "claude-opus-4-6": {
        "betas": BETAS_BASE + ["context-1m-2025-08-07", "effort-2025-11-24"],
        "thinking": {"type": "adaptive"},
        "max_tokens": 21333,
        "output_config": {"effort": "high"},
    },
    "claude-haiku-4-5-20251001": {
        "betas": BETAS_BASE,
        "thinking": {"type": "enabled", "budget_tokens": 21332},
        "max_tokens": 21333,
    },
}

MAX_FAILURES = int(os.environ.get("CLAUDE_MAX_FAILURES", "3"))
COOLDOWN_SECONDS = float(os.environ.get("CLAUDE_COOLDOWN_SECONDS", "120"))


class _ClaudeAccount:
    """Single Claude OAuth account."""

    def __init__(self, label, access_token, refresh_token=None, credentials_file=None):
        self.label = label
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.credentials_file = credentials_file
        self.expires_at = time.time() + 3600
        self.failure_count = 0
        self.disabled_until = 0

    @property
    def is_available(self):
        return time.time() >= self.disabled_until

    def mark_failure(self):
        self.failure_count += 1
        if self.failure_count >= MAX_FAILURES:
            self.disabled_until = time.time() + COOLDOWN_SECONDS
            print(f"[claude] account '{self.label}' disabled for {COOLDOWN_SECONDS}s after {self.failure_count} failures")

    def mark_success(self):
        self.failure_count = 0

    def get_token(self):
        if time.time() > self.expires_at - 300:
            self._refresh()
        return self.access_token

    def _refresh(self):
        if not self.refresh_token:
            return
        try:
            resp = requests.post(OAUTH_TOKEN_URL, json={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            }, headers={"Content-Type": "application/json"}, timeout=10)
            if resp.ok:
                data = resp.json()
                self.access_token = data.get("access_token", self.access_token)
                if data.get("refresh_token"):
                    self.refresh_token = data["refresh_token"]
                self.expires_at = time.time() + data.get("expires_in", 3600)
                self._save_tokens()
                print(f"[claude] account '{self.label}' token refreshed")
            else:
                print(f"[claude] account '{self.label}' refresh failed: {resp.status_code}")
        except Exception as e:
            print(f"[claude] account '{self.label}' refresh error: {e}")

    def _save_tokens(self):
        if not self.credentials_file:
            return
        try:
            with open(self.credentials_file) as f:
                data = json.load(f)
            oauth = data.get("claudeAiOauth", {})
            oauth["accessToken"] = self.access_token
            oauth["refreshToken"] = self.refresh_token
            oauth["expiresAt"] = int(self.expires_at * 1000)
            data["claudeAiOauth"] = oauth
            with open(self.credentials_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


class ClaudeClient:
    """Multi-account Claude client with round-robin and failover."""

    def __init__(self):
        self._accounts = []
        self._index = 0
        self._lock = threading.Lock()
        self._load_accounts()

    def _load_accounts(self):
        # 1. Multi-account tokens (comma-separated env vars)
        tokens = os.environ.get("CLAUDE_OAUTH_TOKENS", "")
        refresh_tokens = os.environ.get("CLAUDE_OAUTH_REFRESH_TOKENS", "")
        if tokens:
            token_list = [t.strip() for t in tokens.split(",") if t.strip()]
            refresh_list = [t.strip() for t in refresh_tokens.split(",") if t.strip()] if refresh_tokens else []
            for i, token in enumerate(token_list):
                refresh = refresh_list[i] if i < len(refresh_list) else None
                self._accounts.append(_ClaudeAccount(f"env-{i+1}", token, refresh))
            print(f"[claude] {len(self._accounts)} accounts loaded from CLAUDE_OAUTH_TOKENS")
            return

        # 2. Multi-account credentials files (comma-separated paths)
        cred_files = os.environ.get("CLAUDE_CREDENTIALS_FILES", "")
        if cred_files:
            for i, path in enumerate(cred_files.split(",")):
                path = os.path.expanduser(path.strip())
                if not path:
                    continue
                acc = self._load_from_credentials_file(path, f"file-{i+1}")
                if acc:
                    self._accounts.append(acc)
            if self._accounts:
                print(f"[claude] {len(self._accounts)} accounts loaded from CLAUDE_CREDENTIALS_FILES")
                return

        # 3. Single account env var
        token = os.environ.get("CLAUDE_OAUTH_TOKEN")
        if token:
            refresh = os.environ.get("CLAUDE_OAUTH_REFRESH_TOKEN")
            self._accounts.append(_ClaudeAccount("env", token, refresh))
            print("[claude] 1 account loaded from CLAUDE_OAUTH_TOKEN")
            return

        # 4. Single credentials file
        if os.path.exists(CREDENTIALS_FILE):
            acc = self._load_from_credentials_file(CREDENTIALS_FILE, "credentials-file")
            if acc:
                self._accounts.append(acc)
                print(f"[claude] 1 account loaded from {CREDENTIALS_FILE}")
                return

        # 5. macOS Keychain
        if platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    data = json.loads(result.stdout.strip())
                    oauth = data.get("claudeAiOauth", {})
                    acc = _ClaudeAccount("keychain", oauth.get("accessToken"), oauth.get("refreshToken"))
                    acc.expires_at = oauth.get("expiresAt", 0) / 1000
                    self._accounts.append(acc)
                    print("[claude] 1 account loaded from macOS Keychain")
                    return
            except Exception as e:
                print(f"[claude] keychain error: {e}")

        print("[claude] no credentials found")

    @staticmethod
    def _load_from_credentials_file(path, label):
        try:
            with open(path) as f:
                data = json.load(f)
            oauth = data.get("claudeAiOauth", {})
            acc = _ClaudeAccount(label, oauth.get("accessToken"), oauth.get("refreshToken"), credentials_file=path)
            acc.expires_at = oauth.get("expiresAt", 0) / 1000
            return acc
        except Exception as e:
            print(f"[claude] credentials file error ({path}): {e}")
            return None

    def _next_account(self):
        """Round-robin with failover: skip disabled accounts."""
        with self._lock:
            n = len(self._accounts)
            if n == 0:
                return None
            for _ in range(n):
                acc = self._accounts[self._index % n]
                self._index += 1
                if acc.is_available:
                    return acc
            # All disabled — return first anyway (cooldown may expire soon)
            return self._accounts[0]

    def get_account(self):
        return self._next_account()

    def build_headers(self, model, account=None):
        acc = account or self._next_account()
        if not acc:
            return {}
        token = acc.get_token()
        config = MODEL_CONFIG.get(model, MODEL_CONFIG["claude-sonnet-4-6"])
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": f"claude-cli/{CLAUDE_CODE_VERSION} (external, cli)",
            "Anthropic-Version": ANTHROPIC_VERSION,
            "Anthropic-Beta": ",".join(config["betas"]),
            "Anthropic-Dangerous-Direct-Browser-Access": "true",
            "X-App": "cli",
            "X-Stainless-Arch": "arm64",
            "X-Stainless-Lang": "js",
            "X-Stainless-Os": "MacOS",
            "X-Stainless-Package-Version": "0.74.0",
            "X-Stainless-Runtime": "node",
            "X-Stainless-Runtime-Version": "v24.3.0",
            "X-Stainless-Timeout": "600",
        }

    @staticmethod
    def build_metadata():
        return {
            "user_id": f"user_{uuid.uuid4().hex}{uuid.uuid4().hex[:32]}_account_{uuid.uuid4()}_session_{uuid.uuid4()}"
        }

    @property
    def account_count(self):
        return len(self._accounts)
