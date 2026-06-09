# CAD models

Every component's 3D mesh as a `.glb`. Served by the orchestrator at
`/static/CAD/<name>.glb` and loaded by the Three.js viewer.

## Adding a new model

1. Drop the `.glb` in this folder.
2. Run `./compress.sh` — Draco-compresses every GLB in place.
   Re-running is idempotent; already-compressed files stay
   (near-)the same size.
3. Commit. The compressed binary lands in git.

The viewer reads compressed and uncompressed GLBs transparently
(the GLTFLoader with DRACOLoader handles both), so forgetting to
run the script just means a bigger transfer for that file — not
a broken model.

## First-time setup (one-time, needs internet)

```bash
# Install nodejs (Pi / Debian — picks an LTS version):
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install the compressor globally:
sudo npm install -g @gltf-transform/cli
```

The runtime never needs internet — the Draco decoder is vendored at
`workspace/gui/vendor/three-addons/draco/`.

## Expected sizes after compression

| File | Before | After (typical) |
|---|---|---|
| `decapper.glb` | 5.6 MB | ~0.8 MB |
| `capfeeder_autosampler_2ml.glb` | 4.1 MB | ~0.5 MB |
| `feeder_cap_2ml.glb` | 2.6 MB | ~0.4 MB |
| `rail_hd_carriage.glb` | 1.6 MB | ~0.25 MB |
| **Folder total** | **~49 MB** | **~5–8 MB** |
