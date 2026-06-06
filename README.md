# Dorna Workspace

A Python SDK + Tornado orchestrator + 3D viewer + MQTT device bus for
robotic lab automation. Manages Dorna robots, custom components
(racks, tools, fixtures), USB / serial / network devices, and
behaviour-tree-planned workflows from a single web UI.

## Documentation

- **For human contributors**: start at [`docs/project-guide.md`](docs/project-guide.md)
  for the workflow / project model, then the relevant guide for what
  you're working on — [`device-guide.md`](docs/device-guide.md),
  [`component-guide.md`](docs/component-guide.md),
  [`recipe-guide.md`](docs/recipe-guide.md), or
  [`bt-framework-guide.md`](docs/bt-framework-guide.md).
- **For AI / Claude sessions**: read [`CLAUDE.md`](CLAUDE.md) and
  [`.claude/skills/README.md`](.claude/skills/README.md) — the task-
  focused playbook with conventions and pointers to canonical doc
  sections.

# Installation

Install the workspace package (run once, or after a fresh OS setup):

```bash
sudo pip3 install --break-system-packages -e /home/dorna/Downloads/workspace/workspace/
```

This installs it as an editable package — code changes are live, no reinstall needed.

# Remote access
```
ssh -p 52022 <device_username>@<your_public_ip>
```
# Finding a USB-serial port (pipette, syringe, multimeter, etc.)

`/dev/ttyUSB0` enumeration order is NOT stable across reboots / replugs.
Use the udev-managed by-id path instead — it survives both:

```bash
ls -d /dev/serial/by-id/*
```

Paste the full `/dev/serial/by-id/<symlink>` path into the device's
`port` field in scene yaml. See [`docs/device-guide.md` §9](docs/device-guide.md)
for the full discovery walk + caveats (generic CP2102 serials, etc.).

# Server
```bash
cd /home/dorna/Downloads/workspace/workspace
sudo python3 gui/server.py
```

This starts both the orchestrator and scene builder on one port:
- Orchestrator: `http://<ip>:5000/orchestrator/`
- Scene Builder: `http://<ip>:5000/scene-builder/`

# pick or place function
Grippers has an anchor called `tcp`
Solids has two anchors `center`  
- `top` is later be matched with `gripping_point` for picking  
- `center` is later be matched with the parent anchor for placing  

# Collision boxes
Collision boxes are defined in the component's .py file, typically alongside the anchor definitions. (please take a look at examples)

The collision_box variable is a dictionary that maps each solid part of your component to a list of bounding boxes. This structure allows you to use one or multiple boxes to accurately encapsulate a solid's shape.

Dictionary Structure:

- Key: The name of the solid (e.g., ```"body"```).

- Value: A list of dictionaries, where each dictionary represents a single collision box.

Example: Component with a Single Box

```python
collision_box = {"body":[
                {"pose":[0.0, 0.0, 4.0, 0.0, 0.0, 0.0], "scale":[150.0, 100.0, 8.0]}
        ]}
```
Every collision box must contain the following two properties:

- ```"pose"```: A standard Dorna API pose array ```[x, y, z, rx, ry, rz]```. The x, y, z coordinates define the center of the box, and rx, ry, rz define its rotation (which is usually left as zero).

- ```"scale"```: An array ‍‍```[lx, ly, lz]``` that dictates the size of the box along each axis. Note: These values represent the full side lengths, not the half-lengths.

For most simple objects, a single box is sufficient to wrap the shape without leaving large gaps. However, for more complex components, you can define multiple boxes inside the list to create a tighter, more accurate collision boundary.