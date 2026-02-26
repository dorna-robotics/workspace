# projects/printer/main.py
import os
from workspace.workspace import Workspace
from workflow import workflow_fn
from workspace.runtime_server import RuntimeServer

def main():
    ws = Workspace(config_path=["config/base.j2", "config/layout.j2"])
    port = int(os.getenv("PORT", "8000"))
    rt_server = RuntimeServer(runtime=ws.rt, workflow_fn=workflow_fn, workspace=ws, port=port)
    rt_server.run()

if __name__ == "__main__":
    main()