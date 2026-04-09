#!/bin/bash
# license_remote.sh — Generate a license on a remote Pi via SSH.
#
# Usage:
#   bash scripts/license_remote.sh <pi-ip> [tier] [ssh-user]
#
# Examples:
#   bash scripts/license_remote.sh 192.168.1.50                    # default tier
#   bash scripts/license_remote.sh 192.168.1.50 pro                # pro tier
#   bash scripts/license_remote.sh 192.168.1.50 default dorna      # custom SSH user
#
# What it does:
#   1. SSH into the Pi, read CPU serial from /proc/cpuinfo
#   2. Sign the serial + tier locally (secret key never leaves this machine)
#   3. SSH into the Pi, write the license file to /etc/dorna/.license
#   4. Verify the license works

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SECRET_KEY_FILE="$REPO_ROOT/.secret.key"
LICENSE_PATH="/etc/dorna/.license"

PI_IP="${1:-}"
TIER="${2:-default}"
PI_USER="${3:-dorna}"

if [ -z "$PI_IP" ]; then
    echo "Usage: bash scripts/license_remote.sh <pi-ip> [tier] [ssh-user]"
    echo "  tier:     default, basic, pro, trial, edu (default: 'default')"
    echo "  ssh-user: defaults to 'dorna'"
    exit 1
fi

# Read secret key
if [ ! -f "$SECRET_KEY_FILE" ]; then
    echo "ERROR: Secret key not found at $SECRET_KEY_FILE"
    exit 1
fi
SECRET_KEY=$(cat "$SECRET_KEY_FILE")

echo "=== Dorna Remote License Generator ==="
echo "Pi:   $PI_USER@$PI_IP"
echo "Tier: $TIER"
echo ""

# Step 1: Read CPU serial from the Pi
echo "--- Reading CPU serial ---"
SERIAL=$(ssh "$PI_USER@$PI_IP" "grep Serial /proc/cpuinfo | awk '{print \$3}'")

if [ -z "$SERIAL" ]; then
    echo "ERROR: Could not read CPU serial from $PI_IP"
    exit 1
fi
echo "  Serial: $SERIAL"

# Step 2: Sign locally (serial + tier)
echo "--- Signing license ---"
SIGNATURE=$(python3 -c "
import hmac, hashlib
secret = b'$SECRET_KEY'
payload = '$SERIAL:$TIER'
print(hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest())
")
echo "  Signature: ${SIGNATURE:0:16}..."

# Step 3: Write license to the Pi
echo "--- Writing license to Pi ---"
ssh "$PI_USER@$PI_IP" "sudo mkdir -p $(dirname $LICENSE_PATH) && printf '%s\n' '$SERIAL' '$TIER' '$SIGNATURE' | sudo tee $LICENSE_PATH > /dev/null"
echo "  Written to: $LICENSE_PATH"

# Step 4: Verify
echo "--- Verifying ---"
VERIFY_RESULT=$(ssh "$PI_USER@$PI_IP" "cat $LICENSE_PATH | head -1")
VERIFY_TIER=$(ssh "$PI_USER@$PI_IP" "sed -n '2p' $LICENSE_PATH")
if [ "$VERIFY_RESULT" = "$SERIAL" ] && [ "$VERIFY_TIER" = "$TIER" ]; then
    echo "  License OK. Tier: $TIER"
else
    echo "  ERROR: Verification failed!"
    exit 1
fi

echo ""
echo "=== Done — $PI_IP is licensed (tier: $TIER) ==="
