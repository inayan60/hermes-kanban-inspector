"""Agent-side entrypoint for the unified Bot Inspector package.

The UI is in desktop/plugin.js and the HTTP API in dashboard/plugin_api.py.
No agent tools, hooks or autonomous work are registered.
"""


def register(ctx):
    """Participate in Hermes plugin registration without adding agent capabilities."""
    return None
