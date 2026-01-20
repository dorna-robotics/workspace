# Remote access
```
ssh -p 52022 <device_username>@<your_public_ip>
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

