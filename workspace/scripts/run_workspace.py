# scripts/run_workspace.py
import time
from pathlib import Path
from workspace import Workspace

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"

def main():
    # Initialize workspace (starts Display automatically)
    ws = Workspace(config_path=str(CONFIG_PATH))
    print("[run_workspace] workspace initialized, running workflow...", flush=True)

    try:
        while True:
            time.sleep(1)  # keep the main thread alive
    except KeyboardInterrupt:
        print("\n[run_workspace] Ctrl+C received, shutting down...", flush=True)
    finally:
        print("[run_workspace] stopping workspace...", flush=True)
        ws.stop()

if __name__ == "__main__":
    main()
