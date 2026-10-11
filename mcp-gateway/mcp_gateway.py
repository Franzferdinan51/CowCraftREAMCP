#!/usr/bin/env python3
"""
mcp_gateway.py — Minta MCP gateway entry point.

Selects the backend via BACKEND env var and launches gateway.py's server:

    BACKEND=stub     in-memory fake world (default; protocol development)
    BACKEND=cowcraft proxy to the real https://cowcraft.world/mcp
                     (CowCraftBackend; set MINTA_COWCRAFT_URL to override)
    BACKEND=live     your own CMaNGOS wiring (live_backend.WoWBackendImpl)

Usage:
    BACKEND=cowcraft python3 mcp_gateway.py --port 8901
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gateway  # noqa: E402


def select_backend():
    mode = os.environ.get("BACKEND", "stub").lower()
    if mode == "cowcraft":
        from cowcraft_backend import CowCraftBackend
        print("backend: cowcraft proxy ->",
              os.environ.get("MINTA_COWCRAFT_URL",
                             "https://cowcraft.world/mcp"), flush=True)
        return CowCraftBackend()
    if mode == "live":
        from live_backend import WoWBackendImpl
        print("backend: live (CMaNGOS)", flush=True)
        return WoWBackendImpl()
    print("backend: stub (in-memory fake world)", flush=True)
    from backend import StubBackend
    return StubBackend()


if __name__ == "__main__":
    gateway.backend = select_backend()
    gateway.main()
