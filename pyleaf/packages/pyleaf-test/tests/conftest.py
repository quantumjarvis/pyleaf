"""pyleaf-test's own suite runs the plugin in isolated in-process pytest
runs via the ``pytester`` fixture."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest_plugins = "pytester"
