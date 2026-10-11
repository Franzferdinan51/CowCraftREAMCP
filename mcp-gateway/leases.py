"""
Lease and connection-token management for the Minta MCP gateway.

Replicates CowCraft's observed behavior:
- Connection token format: UUIDv4.HMAC-SHA256  (<uuid>.<64 hex chars>)
- Lease lifecycle: PREAUTH (~600s) -> AUTHENTICATED -> IN_WORLD (~120s)
- heartbeat()/observe() renew the lease
- One lease per account (account_busy on double-login)
- Stale leases ARE reaped (CowCraft's bug is deliberately NOT copied)
"""

import hashlib
import hmac
import os
import secrets
import threading
import time
import uuid

# Lease durations (seconds) — match CowCraft's observed values
PREAUTH_LEASE_S = 600
WORLD_LEASE_S = 120
# Reap leases idle longer than this (CowCraft never reaps; we do)
STALE_REAP_S = 300


class LeaseState:
    PREAUTH = "preauth"
    AUTHENTICATED = "authenticated"
    IN_WORLD = "in_world"
    EXPIRED = "expired"


class Lease:
    def __init__(self, token, model_name):
        self.token = token
        self.model_name = model_name
        self.state = LeaseState.PREAUTH
        self.account = None          # set on login
        self.character_guid = None   # set on select_character
        self.created_at = time.time()
        self.expires_at = self.created_at + PREAUTH_LEASE_S
        self.last_activity = self.created_at
        # Rate-limit bookkeeping
        self._obs_timestamps = []    # observe/heartbeat calls (1/sec budget)
        self._action_timestamps = [] # action calls (20/sec budget)
        self._nav_timestamps = []    # navigation reads (1/sec, not observe)
        self._map_at = 0.0           # observe_map (5 seconds)
        self._lock = threading.Lock()

    def touch(self):
        """Renew the lease clock. Called on every tool result/error."""
        now = time.time()
        self.last_activity = now
        if self.state == LeaseState.IN_WORLD:
            self.expires_at = now + WORLD_LEASE_S
        else:
            self.expires_at = now + PREAUTH_LEASE_S

    def is_expired(self):
        return time.time() > self.expires_at

    def is_stale(self):
        return time.time() - self.last_activity > STALE_REAP_S

    def check_observe_budget(self):
        """1/sec shared budget for observe + heartbeat. Returns retry_after_ms or 0."""
        now = time.time()
        with self._lock:
            self._obs_timestamps = [t for t in self._obs_timestamps if now - t < 1.0]
            if self._obs_timestamps:
                oldest = min(self._obs_timestamps)
                wait_ms = int((1.0 - (now - oldest)) * 1000) + 1
                return max(wait_ms, 1)
            self._obs_timestamps.append(now)
            return 0

    def check_action_budget(self):
        """20/sec budget for action calls. Returns retry_after_ms or 0."""
        now = time.time()
        with self._lock:
            self._action_timestamps = [t for t in self._action_timestamps if now - t < 1.0]
            if len(self._action_timestamps) >= 20:
                oldest = min(self._action_timestamps)
                wait_ms = int((1.0 - (now - oldest)) * 1000) + 1
                return max(wait_ms, 1)
            self._action_timestamps.append(now)
            return 0

    def check_nav_budget(self):
        """1/sec shared by navigation reads and observe_map."""
        now = time.time()
        with self._lock:
            self._nav_timestamps = [t for t in self._nav_timestamps if now - t < 1.0]
            if self._nav_timestamps:
                oldest = min(self._nav_timestamps)
                wait_ms = int((1.0 - (now - oldest)) * 1000) + 1
                return max(wait_ms, 1)
            self._nav_timestamps.append(now)
            return 0

    def check_map_budget(self):
        """observe_map at most once every 5 seconds. Also uses the nav slot."""
        now = time.time()
        with self._lock:
            if self._map_at and now - self._map_at < 5.0:
                wait_ms = int((5.0 - (now - self._map_at)) * 1000) + 1
                return max(wait_ms, 1)
        wait_ms = self.check_nav_budget()
        if wait_ms:
            return wait_ms
        with self._lock:
            self._map_at = time.time()
        return 0


class LeaseManager:
    """
    Issues tokens, tracks leases, enforces one-lease-per-account.

    Token format (from RE): <uuid4>.<hmac-sha256 hex>
    The HMAC is keyed with the server secret, so tokens are self-validating.
    """

    def __init__(self, secret=None):
        self.secret = secret or os.environ.get(
            "MINTA_HMAC_SECRET", secrets.token_hex(32)
        )
        self._leases = {}        # token -> Lease
        self._account_lease = {} # username.lower() -> token
        self._lock = threading.Lock()
        # Background reaper
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    # -- token crypto -------------------------------------------------

    def _sign(self, session_uuid):
        return hmac.new(
            self.secret.encode(), session_uuid.encode(), hashlib.sha256
        ).hexdigest()

    def _make_token(self):
        session_uuid = str(uuid.uuid4())
        return f"{session_uuid}.{self._sign(session_uuid)}"

    def _verify(self, token):
        try:
            session_uuid, sig = token.split(".", 1)
            uuid.UUID(session_uuid)  # validates format
            return hmac.compare_digest(sig, self._sign(session_uuid))
        except (ValueError, AttributeError):
            return False

    # -- lease lifecycle ----------------------------------------------

    def create_lease(self, model_name):
        token = self._make_token()
        lease = Lease(token, model_name)
        with self._lock:
            self._leases[token] = lease
        return lease

    def get_lease(self, token):
        """Returns (lease, error_code). error_code is None on success."""
        if not token or not self._verify(token):
            return None, "invalid_connection"
        with self._lock:
            lease = self._leases.get(token)
        if lease is None:
            return None, "invalid_connection"
        if lease.is_expired():
            self._drop(token)
            return None, "lease_expired"
        return lease, None

    def bind_account(self, lease, username):
        """
        Bind a lease to an account. Enforces one-lease-per-account.
        Returns error_code or None.
        """
        key = username.lower()
        with self._lock:
            existing = self._account_lease.get(key)
            if existing and existing != lease.token:
                old = self._leases.get(existing)
                if old is not None and not old.is_expired() and not old.is_stale():
                    return "account_busy"
                # stale/expired old lease: take it over
                self._drop(existing)
            self._account_lease[key] = lease.token
        lease.account = username
        lease.state = LeaseState.AUTHENTICATED
        lease.touch()
        return None

    def bind_character(self, lease, character_guid):
        lease.character_guid = character_guid
        lease.state = LeaseState.IN_WORLD
        lease.touch()

    def release(self, token):
        """Disconnect: drop the lease (idempotent)."""
        with self._lock:
            self._drop(token)

    def _drop(self, token):
        lease = self._leases.pop(token, None)
        if lease and lease.account:
            if self._account_lease.get(lease.account.lower()) == token:
                del self._account_lease[lease.account.lower()]

    def _reap_loop(self):
        while True:
            time.sleep(30)
            with self._lock:
                dead = [
                    t for t, l in self._leases.items()
                    if l.is_expired() or l.is_stale()
                ]
                for t in dead:
                    self._drop(t)
