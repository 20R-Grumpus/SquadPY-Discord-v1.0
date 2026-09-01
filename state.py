"""Unified runtime state.

Replaces the module-level mutable globals from the original monolith with a
single `BotState` instance, plus a unified JSON persistence store shared by all
features (seedtrack, rotation, sticky, and future sections).
"""

import json
import os

from config import (
    UNIFIED_STATE_FILE,
    POP_TRACK_FILE,
    ROTATION_TRACK_FILE,
    STICKY_STATE_FILE,
)


class BotState:
    def __init__(self):
        # Seeding / background task state
        self.last_seed_trigger_time = None
        self.scheduled_seeding_time = None
        self.scheduled_task = None
        self.seeding_started = False
        self.seeding_triggered_at = 0
        self.posted_milestones = set()
        self.milestone_messages = []

        # Background task references
        self.background_task_ref = None
        self.rotation_task_ref = None
        self.log_sender_task_ref = None
        self.join_link_updater_task_ref = None
        self.whitelist_task_ref = None

        # Rotation state
        self.rotation_message_id = None
        self.rotation_channel_id = None
        self.cached_server_name = None

        # Shared HTTP session
        self.http_session = None

        # Sticky message state
        self.LAST_STICKY_MESSAGE_ID = None


state = BotState()


# ---------------------------------------------------------------------------
# Unified persistent state store
#
# All persistent state lives in a single JSON file keyed by section name
# (e.g. "seedtrack", "rotation", "sticky"). New code should use
# `update_state(section, ...)` to add information without overwriting other
# keys, `get_state(section)` to read, and `set_state(section, data)` to replace
# a whole section.
# ---------------------------------------------------------------------------

# Legacy single-purpose files migrated into the unified store on first read.
_LEGACY_FILES = {
    "seedtrack": POP_TRACK_FILE,
    "rotation": ROTATION_TRACK_FILE,
    "sticky": STICKY_STATE_FILE,
}


def _read_store():
    if os.path.exists(UNIFIED_STATE_FILE):
        try:
            with open(UNIFIED_STATE_FILE) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    # First run with the unified store: pull in any legacy per-feature files.
    store = {}
    for section, path in _LEGACY_FILES.items():
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    store[section] = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
    if store:
        _write_store(store)
    return store


def _write_store(store):
    tmp_path = f"{UNIFIED_STATE_FILE}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(store, f, indent=2)
    os.replace(tmp_path, UNIFIED_STATE_FILE)


def get_state(section, default=None):
    """Return a section of the store, or `default` ({} if unset) when missing."""
    return _read_store().get(section, {} if default is None else default)


def set_state(section, data):
    """Replace an entire section of the store."""
    store = _read_store()
    store[section] = data
    _write_store(store)


def update_state(section, data=None, **values):
    """Safely merge keys into a section without clobbering existing data.

    Accepts a dict and/or keyword arguments:
        update_state("rotation", {"message_id": 123})
        update_state("rotation", message_id=123)
    """
    merged = {}
    if data:
        merged.update(data)
    merged.update(values)

    store = _read_store()
    current = store.get(section)
    if not isinstance(current, dict):
        current = {}
    current.update(merged)
    store[section] = current
    _write_store(store)
    return current


# Backward-compatible wrappers over the unified store.
def save_state(data):
    set_state("seedtrack", data)


def load_state():
    return get_state("seedtrack", {})


def save_rotation_state(data):
    set_state("rotation", data)


def load_rotation_state():
    return get_state("rotation", {})
