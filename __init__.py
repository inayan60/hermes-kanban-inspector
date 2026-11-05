"""Unified Hermes Bot Inspector Agent half.

The read-only (except explicitly confirmed Git child recovery) HTTP endpoints
are mounted from dashboard/plugin_api.py. No Agent tools or hooks are added.
"""

def register(ctx):
    """Satisfy Hermes Agent plugin registration without adding model tools."""
    return None