import os
import argparse

from workspace.workspace import Workspace
from workflow import workflow_fn
from workspace.runtime_server import RuntimeServer

def main():
    # port
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = p.parse_args()
    port = int(args.port)

    ws = Workspace(config_path=["config/base.j2", "config/layout.j2"], port=port)
    rt_server = RuntimeServer(runtime=ws.rt, workflow_fn=workflow_fn, workspace=ws)
    rt_server.run()

if __name__ == "__main__":
    main()