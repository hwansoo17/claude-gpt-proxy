"""
Codex API client via direct HTTP (OAuth).
Supports multi-account with round-robin and failover.
Docker 환경에서도 토큰 갱신이 정상 동작하도록 설계.
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

# 토큰 만료 전 갱신 여유 시간 (초)
REFRESH_MARGIN = int(os.environ.get("CODEX_REFRESH_MARGIN", "300"))

# 백그라운드 토큰 갱신 주기 (초, 기본 30분)
KEEPALIVE_INTERVAL = int(os.environ.get("CODEX_KEEPALIVE_INTERVAL", "1800"))


class _CodexAccount:
    """Single Codex OAuth account."""

    def __init__(self, label, access_token, refresh_token=None, auth_file=None, expires_at=None):
        self.label = label
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.auth_file = auth_file
        # auth.json에 expires_at이 있으면 사용, 없으면 1시간 후 갱신 시도
        self.expires_at = expires_at or (time.time() + 3600)
        self.failure_count = 0
        self.disabled_until = 0
        self._refresh_lock = threading.Lock()

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

    def ensure_valid_token(self):
        """토큰이 만료 임박하면 갱신. 401 발생 시 강제 갱신용으로도 사용."""
        if not self.refresh_token:
            return
        if time.time() < self.expires_at - REFRESH_MARGIN:
            return
        self._do_refresh()

    def force_refresh(self):
        """401 등으로 토큰이 유효하지 않을 때 강제 갱신."""
        if not self.refresh_token:
            return False
        return self._do_refresh()

    def _do_refresh(self):
        with self._refresh_lock:
            # 다른 스레드가 이미 갱신했을 수 있으므로 재확인
            if time.time() < self.expires_at - REFRESH_MARGIN:
                return True
            try:
                resp = requests.post(TOKEN_URL, data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": CLIENT_ID,
                    "scope": "openid profile email offline_access",
                }, headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                }, timeout=15)
                if resp.ok:
                    tokens = resp.json()
                    self.access_token = tokens["access_token"]
                    if tokens.get("refresh_token"):
                        self.refresh_token = tokens["refresh_token"]
                    self.expires_at = time.time() + tokens.get("expires_in", 86400)
                    self._save_tokens()
                    print(f"[codex] account '{self.label}' token refreshed (expires in {tokens.get('expires_in', '?')}s)")
                    return True
                else:
                    print(f"[codex] account '{self.label}' refresh failed: {resp.status_code} {resp.text[:200]}")
                    return False
            except Exception as e:
                print(f"[codex] account '{self.label}' refresh error: {e}")
                return False

    def _save_tokens(self):
        if not self.auth_file:
            return
        try:
            # 기존 파일 읽어서 업데이트 (다른 필드 보존)
            try:
                with open(self.auth_file) as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}

            data["tokens"] = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
            }
            data["expires_at"] = self.expires_at
            data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S")

            with open(self.auth_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[codex] account '{self.label}' save error: {e}")

    def build_headers(self):
        self.ensure_valid_token()
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
        self._start_keepalive()

    def _start_keepalive(self):
        """백그라운드에서 주기적으로 토큰 갱신. 서버가 오래 유휴 상태여도 토큰이 만료되지 않도록 함."""
        if not self._accounts:
            return

        def _loop():
            while True:
                time.sleep(KEEPALIVE_INTERVAL)
                for acc in self._accounts:
                    if not acc.refresh_token:
                        continue
                    remaining = acc.expires_at - time.time()
                    if remaining < KEEPALIVE_INTERVAL + REFRESH_MARGIN:
                        print(f"[codex] keepalive: refreshing '{acc.label}' (expires in {int(remaining)}s)")
                        acc._do_refresh()
                    else:
                        print(f"[codex] keepalive: '{acc.label}' OK (expires in {int(remaining)}s)")

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        print(f"[codex] keepalive started (interval={KEEPALIVE_INTERVAL}s)")

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
            print(f"[codex] 1 account loaded from {auth_file}")
        else:
            print(f"[codex] WARNING: auth file not found or invalid: {auth_file}")

    @staticmethod
    def _load_from_file(path, label):
        try:
            with open(path) as f:
                data = json.load(f)
            tokens = data.get("tokens", {})
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            if not access_token:
                print(f"[codex] auth file has no access_token ({path})")
                return None
            # expires_at: 파일에 저장된 값 사용
            expires_at = data.get("expires_at")
            return _CodexAccount(
                label=label,
                access_token=access_token,
                refresh_token=refresh_token,
                auth_file=path,
                expires_at=expires_at,
            )
        except FileNotFoundError:
            print(f"[codex] auth file not found: {path}")
            return None
        except json.JSONDecodeError as e:
            print(f"[codex] auth file invalid JSON ({path}): {e}")
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
            yield "error", {"error": "No codex accounts configured. Check CODEX_AUTH_FILE or CODEX_AUTH_FILES env var."}
            return

        headers = acc.build_headers()
        resp = requests.post(CODEX_API_URL, headers=headers, json=body, stream=True, timeout=300)

        # 401 → 토큰 갱신 후 재시도 (1회)
        if resp.status_code == 401 and acc.force_refresh():
            print(f"[codex] 401 received, retrying with refreshed token (account '{acc.label}')")
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
