"""Channel connectors (Zalo OA, Facebook Messenger, Google Calendar).

Kept import-light on purpose: ``app.models`` imports ``app.connectors.models`` to
register the table, and the concrete modules import ``app.crypto`` → ``app.auth`` →
``app.models``, so pulling them in here would create a cycle. Routers import the
concrete modules themselves.
"""
