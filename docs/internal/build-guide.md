# Build & Ship Guide (Internal)

How to build a release, prepare an SD card, and ship a Pi to a customer.

---

## Prerequisites

On your build machine (must be ARM — a Raspberry Pi):

```bash
sudo pip3 install --break-system-packages cython
```

---

## 1. Build a release

From the private repo:

```bash
cd /home/dorna/Downloads/workspace
bash scripts/build_release.sh
```

This will:
1. Compile files listed in `protected.txt` to `.so`
2. Sync everything to `/home/dorna/Downloads/workspace-release/`
3. Remove excluded files (`.secret.key`, `scripts/`, `docs/internal/`, `license_admin.py`, etc.)
4. Remove `.py` source for compiled files (only `.so` remains)

Then push to the release repo:

```bash
cd /home/dorna/Downloads/workspace-release
git add -A
git commit -m "Release vX.Y.Z"
git push
```

---

## 2. Prepare an SD card

### Flash the base image

1. Flash your standard Raspberry Pi OS image
2. Boot and complete first-time setup (WiFi, locale, SSH enabled, etc.)

### Install the workspace

SSH into the Pi and run:

```bash
cd /home/dorna/Downloads
git clone https://github.com/dorna/workspace-release.git workspace-release

cd workspace-release/workspace
sudo pip3 install --break-system-packages -e .
```

### Generate the license

**From your admin machine** (not on the Pi):

```bash
cd /home/dorna/Downloads/workspace
bash scripts/license_remote.sh <pi-ip>
```

For example:
```bash
bash scripts/license_remote.sh 192.168.1.50
bash scripts/license_remote.sh 192.168.1.50 dorna    # custom SSH user
```

This will:
1. SSH into the Pi and read the CPU serial
2. Sign the serial locally on your machine (secret key never leaves your machine)
3. SSH into the Pi and write the license to `/etc/dorna/.license`
4. Verify the license was written correctly

**Alternatively**, if you're directly on the Pi with the private repo:

```bash
sudo python3 -m workspace.license_admin generate
```

### Verify (on the Pi)

```bash
sudo python3 -m workspace.license verify
```

Should print: `License OK.`

---

## 3. Test before shipping

```bash
# Start the orchestrator
cd /home/dorna/Downloads/workspace-release/workspace
sudo python3 orchestrator/server.py

# Open in browser
# http://<pi-ip>:5000
```

Launch a workspace, run a workflow, verify everything works.

---

## 4. Ship

The Pi is ready. Customer turns it on, it works.

---

## 5. Updating the release

When you push new code to the private repo:

```bash
# In the private repo
cd /home/dorna/Downloads/workspace
git pull

# Rebuild release
bash scripts/build_release.sh

# Push release
cd /home/dorna/Downloads/workspace-release
git add -A
git commit -m "Release vX.Y.Z"
git push
```

Customer updates by following the upgrade guide (`docs/upgrade-guide.md`).

---

## 6. Managing protected files

Edit `protected.txt` in the private repo to add/remove files that get compiled:

```
# Files to compile to .so in the release build.
workspace/license.py
workspace/runtime.py
workspace/components/core/core.py
```

After editing, rebuild the release with `bash scripts/build_release.sh`.

---

## 7. Secret key

The secret key lives at `.secret.key` in the repo root (git-tracked in the private repo, excluded from release). It's also embedded in `workspace/license.py` — which gets compiled to `.so`, hiding the key.

If you ever need to regenerate:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Update both `.secret.key` and the `_SECRET` value in `workspace/workspace/license.py`. Then rebuild the release. All previously generated licenses become invalid — you'll need to re-license every Pi.

---

## 8. Licensing a Pi remotely

From your admin machine:

```bash
bash scripts/license_remote.sh <pi-ip> [ssh-user]
```

Requirements:
- SSH access to the Pi (default user: `dorna`)
- The private repo with `.secret.key` on your admin machine
- Python 3 on your admin machine (for HMAC signing)

The script never copies the secret key to the Pi. It reads the serial over SSH, signs locally, and writes the license file back over SSH.

---

## 9. Revoking a license

You can't remotely revoke a license. But:
- Changing the secret key invalidates all existing licenses
- A cloned SD card won't work on a different Pi (hardware serial mismatch)
- To re-license a Pi remotely: `bash scripts/license_remote.sh <pi-ip>`
