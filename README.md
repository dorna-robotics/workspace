# Remote access
```
ssh -p 52022 <device_username>@<your_public_ip>
```
# pipette and syringe
use this command to find the port
```
ls /dev/ttyUSB*
```

# Server
```bash
cd /home/dorna/Downloads/workspace/workspace
sudo python3 server.py
```

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

```json
collision_box = {"body":[
                {"pose":[0.0, 0.0, 4.0, 0.0, 0.0, 0.0], "scale":[150.0, 100.0, 8.0]}
        ]}
```
Every collision box must contain the following two properties:

- ```"pose"```: A standard Dorna API pose array ```[x, y, z, rx, ry, rz]```. The x, y, z coordinates define the center of the box, and rx, ry, rz define its rotation (which is usually left as zero).

- ```"scale"```: An array ‍‍```[lx, ly, lz]``` that dictates the size of the box along each axis. Note: These values represent the full side lengths, not the half-lengths.

For most simple objects, a single box is sufficient to wrap the shape without leaving large gaps. However, for more complex components, you can define multiple boxes inside the list to create a tighter, more accurate collision boundary.