import os
from pathlib import Path

RIDB_API_KEY = os.environ.get("RIDB_API_KEY", "")

MONITOR_STATE_FILE = Path.home() / ".cache" / "campground-mcp" / "monitor_state.json"
MONITOR_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
