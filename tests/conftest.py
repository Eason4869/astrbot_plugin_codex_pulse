"""Allow running the plugin's tests outside an AstrBot source checkout."""

import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
for name, path in (
    ("data", ROOT.parent.parent),
    ("data.plugins", ROOT.parent),
    ("data.plugins.astrbot_plugin_codex_reset", ROOT),
):
    if name not in sys.modules:
        module = ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module
