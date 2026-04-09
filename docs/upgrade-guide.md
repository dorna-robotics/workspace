# Upgrade Guide

How to update your Dorna Workspace to the latest version.

---

## Update

```bash
cd /home/dorna/Downloads/workspace
git pull
cd workspace
sudo pip3 install --break-system-packages -e .
```

That's it. Your projects and license are not affected.

---

## Verify

After updating, verify everything works:

```bash
# Check the license is still valid
sudo python3 -m workspace.license verify

# Start the orchestrator
cd /home/dorna/Downloads/workspace/workspace
sudo python3 orchestrator/server.py
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `git pull` fails | Check your internet connection. Try `git status` to see if there are local changes conflicting. |
| `pip install` fails | Make sure you include `--break-system-packages`. |
| License error after update | Your license should survive updates. If not, contact Dorna support. |
| Import errors | Run `sudo pip3 install --break-system-packages -e .` again from the `workspace/` folder. |
