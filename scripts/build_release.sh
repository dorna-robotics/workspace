#!/bin/bash
# build_release.sh — compile protected files and sync to release repo.
#
# Usage:
#   cd /home/dorna/Downloads/workspace
#   bash scripts/build_release.sh
#
# Prerequisites:
#   sudo pip3 install --break-system-packages cython
#
# What it does:
#   1. Reads protected.txt — list of .py files to compile
#   2. Compiles each to .so using Cython
#   3. Copies everything to the release repo
#   4. Removes excluded files/folders (.release-exclude)
#   5. Removes .py source for compiled files (keeps .so)
#   6. Optionally commits and pushes the release repo

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="/home/dorna/Downloads/workspace-release"
PROTECTED_FILE="$REPO_ROOT/protected.txt"
EXCLUDE_FILE="$REPO_ROOT/.release-exclude"
PKG_DIR="$REPO_ROOT/workspace"  # the Python package directory

echo "=== Dorna Workspace Release Builder ==="
echo "Source:  $REPO_ROOT"
echo "Release: $RELEASE_DIR"
echo ""

# --- Step 1: Compile protected files ---
echo "--- Compiling protected files ---"
cd "$PKG_DIR"

while IFS= read -r line; do
    # Skip comments and empty lines
    [[ "$line" =~ ^#.*$ || -z "${line// }" ]] && continue
    py_file="$PKG_DIR/$line"

    if [ ! -f "$py_file" ]; then
        echo "  SKIP (not found): $line"
        continue
    fi

    echo "  Compiling: $line"

    # Flip _DEV = True → False for release builds (restored after compile)
    _dev_patched=false
    if grep -q '_DEV = True' "$py_file" 2>/dev/null; then
        sed -i 's/_DEV = True/_DEV = False/' "$py_file"
        _dev_patched=true
        echo "    Set _DEV = False"
    fi

    cython "$py_file" --embed 2>/dev/null || cython "$py_file" 2>/dev/null

    # Get the .c file and compile to .so
    c_file="${py_file%.py}.c"
    so_name=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
    so_file="${py_file%.py}${so_name}"

    gcc -shared -fPIC -O2 \
        $(python3-config --includes) \
        $(python3-config --ldflags --embed 2>/dev/null || python3-config --ldflags) \
        -o "$so_file" "$c_file"

    # Clean up .c file
    rm -f "$c_file"

    # Restore _DEV = True in source repo
    if [ "$_dev_patched" = true ]; then
        sed -i 's/_DEV = False/_DEV = True/' "$py_file"
    fi

    echo "    → $(basename "$so_file")"
done < "$PROTECTED_FILE"

echo ""

# --- Step 2: Sync to release repo ---
echo "--- Syncing to release repo ---"

# Create release dir if needed
mkdir -p "$RELEASE_DIR"

# Rsync everything, excluding .git
rsync -a --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.c' \
    "$REPO_ROOT/" "$RELEASE_DIR/"

echo "  Synced."

# --- Step 3: Remove excluded files/folders ---
echo "--- Removing excluded files ---"
if [ -f "$RELEASE_DIR/.release-exclude" ]; then
    while IFS= read -r line; do
        [[ "$line" =~ ^#.*$ || -z "${line// }" ]] && continue
        target="$RELEASE_DIR/$line"
        if [ -e "$target" ]; then
            rm -rf "$target"
            echo "  Removed: $line"
        fi
    done < "$RELEASE_DIR/.release-exclude"
    # Remove the exclude file itself (already listed but just in case)
    rm -f "$RELEASE_DIR/.release-exclude"
fi

# --- Step 4: Remove .py source for compiled files ---
echo "--- Removing protected .py source ---"
while IFS= read -r line; do
    [[ "$line" =~ ^#.*$ || -z "${line// }" ]] && continue
    py_in_release="$RELEASE_DIR/workspace/$line"
    if [ -f "$py_in_release" ]; then
        rm -f "$py_in_release"
        echo "  Removed: workspace/$line"
    fi
done < "$PROTECTED_FILE"

# Also remove protected.txt from release (belt and suspenders)
rm -f "$RELEASE_DIR/protected.txt"

# --- Step 5: Fix .gitignore — remove *.so so compiled files can be committed ---
echo "--- Fixing .gitignore for release ---"
if [ -f "$RELEASE_DIR/.gitignore" ]; then
    sed -i '/^\*\.so$/d' "$RELEASE_DIR/.gitignore"
    echo "  Removed *.so from .gitignore"
fi

echo ""

# --- Step 6: Clean compiled .so from source repo ---
echo "--- Cleaning .so from source repo ---"
find "$PKG_DIR" -name "*.so" -delete
echo "  Done."

echo ""
echo "=== Release build complete ==="
echo "Release at: $RELEASE_DIR"
echo ""
echo "To commit and push the release:"
echo "  cd $RELEASE_DIR"
echo "  git add -A && git commit -m 'Release vX.Y.Z' && git push"
