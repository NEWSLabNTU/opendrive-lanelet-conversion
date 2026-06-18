"""Load user-editable Autoware conversion defaults and apply them to a Lanelet2Config.

The OpenDRIVE source cannot supply every Autoware tag (e.g. a lane with no
MAX_SPEED sign has no speed_limit). `config/autoware.yaml` lets the user set the
fallback values; this module reads that file and pushes the values onto the
crdesigner ``lanelet2_config`` object before conversion.
"""
from pathlib import Path

import yaml

# Default config shipped with the repo. convert.py / demo_evan.py use this when
# no explicit path is given.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "autoware.yaml"


def load_autoware_config(path=None):
    """Return the `autoware:` section of the YAML config as a dict.

    Missing file or missing section yields an empty dict, so callers fall back to
    crdesigner's built-in defaults.
    """
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("autoware", {}) or {}


def apply_autoware_config(lanelet2_config, path=None):
    """Apply repo-side Autoware defaults onto a crdesigner ``lanelet2_config``.

    Only keys present in the file override the crdesigner built-ins. Returns the
    config dict that was applied (for logging).
    """
    cfg = load_autoware_config(path)

    speed = cfg.get("default_speed_kmh")
    if speed:
        # crdesigner enforces dict type on this attribute; floats read cleanly downstream.
        lanelet2_config.autoware_default_speed_kmh = {k: float(v) for k, v in speed.items()}

    return cfg
