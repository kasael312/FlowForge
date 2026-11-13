"""Industry data layer ("biz"): customers, services, memberships, appointments, staff, inbox.

Every table here carries ``workspace_id``; routers resolve the workspace with
``app.workspace.CurrentWorkspace`` and filter on it unconditionally.
"""
