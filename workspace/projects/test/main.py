"""projects/test/main.py — minimal Core 500 + vision station smoke test.

Drives a single workflow state (``rotated``) through BaseWorkflow so
the operator can exercise the device-bus integration end-to-end on
real hardware:

  * Robot ``dorna:<ip>`` shows up in the Devices panel via Core's
    RobotStation wrapping; connection drops + alarms turn the dot red,
    AutoRecover retries reconnects automatically.
  * Camera ``camera:<serial>`` shows up via the vision_station's
    inspection adapter; USB unplug + freshness checks surface to the
    Devices panel, ``inspector.detect()`` raises CameraUnavailableError
    if the bus reports the camera offline.

See ``scene/base.j2`` for the scene; the camera serial number, robot
IP, and vision-server host are filled in there.
"""

import os
import argparse
from pathlib import Path

import yaml

from workspace.workspace import Workspace
from workspace.ortools.workflow import BaseWorkflow
from workspace.runtime_server import RuntimeServer
from states import States
from checks import Checks

_BASE_DIR = Path(__file__).parent


def workflow_fn(*, workspace, core, **kwargs):
    BaseWorkflow(workspace, core, _BASE_DIR, States, Checks, **kwargs).run()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "5010")))
    args = p.parse_args()

    with open(_BASE_DIR / "launch.yaml") as f:
        launch = yaml.safe_load(f)

    ws = Workspace(config_path=launch["scene"], port=args.port)
    RuntimeServer(runtime=ws.rt, workflow_fn=workflow_fn, workspace=ws).run()


if __name__ == "__main__":
    main()
