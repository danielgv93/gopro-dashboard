"""Runs gopro-dashboard.py with this app's extra widgets registered (python -m gopro_daily.dashboard <args>)."""

from __future__ import annotations

import runpy
import shutil
import sys
from pathlib import Path


def dashboard_script() -> str:
    local = Path(sys.executable).parent / "gopro-dashboard.py"
    if local.exists():
        return str(local)
    found = shutil.which("gopro-dashboard.py")
    if not found:
        raise SystemExit("gopro-dashboard.py not found (is gopro-overlay installed in this environment?)")
    return found


def main() -> None:
    from .day_journey_map import register
    from .track import lift_gps_timeout

    register()
    lift_gps_timeout()
    script = dashboard_script()
    sys.argv = [script, *sys.argv[1:]]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
