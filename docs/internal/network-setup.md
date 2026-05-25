# Network Setup — On-Site Cabinet

How to configure networking for a deployed Dorna workspace cabinet:
static IP scheme for the devices on the switch, plus the on-site
internet path via the Samsung tablet acting as a temporary gateway.

## 1. Architecture at a glance

The cabinet ships with:

- A switch (dumb layer-2, no routing)
- Orchestrator Pi, vision Pi, robot, camera, scale, printer — all on
  the switch with **static IPs**
- Samsung Android tablet — also plugged into the switch via a
  USB-ethernet adapter

There is **no permanent internet uplink**. Devices talk to each
other on the LAN for normal operation. When a tech is on-site and
needs internet (apt update, ssh in from a laptop via Tailscale,
download a file, etc.), they bring it temporarily through the
tablet via **Android Ethernet tethering**:

```
Customer Wi-Fi  →  Tablet (wlan)  →  Tablet (USB-Eth)  →  Switch  →  All devices
```

When the tech leaves and the tablet sleeps/disconnects, the LAN
keeps working internally; it just no longer has internet. That's
the intended behaviour.

### Why this approach and not alternatives

| Option | Why we didn't pick it |
|---|---|
| Pi runs wifi + NAT | Cabinet is RF-shielded; Pi wifi reception is unusable |
| USB wifi dongle on Pi with external antenna | Works, but more moving parts (USB extension, antenna placement) than the tablet approach when we already have a tablet on-site |
| GL.iNet travel router outside cabinet | The cleanest answer for **24/7 production**. Adopt when we move past the prototype phase. For now, the tablet covers the same need at zero extra hardware cost |
| 4G/LTE modem | Future option for sites with no usable Wi-Fi or hostile IT departments |
| Plug switch uplink into customer ethernet | Customer cooperation rare; loses control of IP scheme |
| iPad as gateway | iPadOS doesn't support ethernet sharing — only cellular Personal Hotspot. **iPad is not an option.** |

## 2. Why 192.168.42.x and not 10.0.0.x

Android's built-in Ethernet tethering **forces** its own subnet on
the tethered interface, typically `192.168.42.0/24`, and runs its
own DHCP server. You cannot change this in the UI without rooting
the tablet.

Rather than fight Android, we adopt its subnet as our LAN range.
Every device on the cabinet's switch gets a static IP inside
`192.168.42.0/24`. The tablet, when tethering is active, becomes
the gateway. When the tablet is absent or tethering is off, the
devices still see each other on the LAN — they just have no
default route to the internet.

### IP scheme

Pick a scheme and stick to it across cabinets so any tech can
predict where to ssh:

| Device | Static IP |
|---|---|
| Orchestrator Pi | `192.168.42.101` |
| Robot | `192.168.42.102` |
| Vision Pi | `192.168.42.103` |
| Camera | `192.168.42.104` |
| Scale | `192.168.42.105` |
| Printer | `192.168.42.106` |
| Tablet (when tethering) | `192.168.42.129` (auto-assigned by Android) |
| Gateway | Whatever the tablet ends up at (see § 4) |

All devices set:
- **Gateway** = the tablet's tethered IP (see § 4)
- **DNS** = `1.1.1.1` (Cloudflare) — always works as long as NAT is up

## 3. Tablet setup (one-time per tablet)

### 3.1 Keep the screen on

Samsung's default behaviour will dim or sleep the screen, breaking
the tethering session. Counter-measures:

1. **Settings → Display → Adaptive brightness → OFF**, then drag
   the brightness slider to a comfortable level. (Without this,
   the screen dims down to nearly invisible after a few seconds of
   no touch, even when "Stay Awake" is on.)
2. **Settings → Display → Screen timeout → 10 minutes** (max).
3. **Install Caffeine** (Play Store, by Sebastian Krzyszkowiak,
   free). Pull down the notification shade, tap the Caffeine
   notification → screen stays on indefinitely. This bypasses every
   Samsung "smart" dimming feature and is the most reliable single
   fix. Use this instead of (or alongside) Developer Options' "Stay
   Awake while charging."
4. Plug the tablet into a **wall charger** (15W USB-C brick).
   Laptop USB-A ports often don't deliver enough current and the
   tablet won't actually charge, which disables Stay Awake.
   Confirm by checking the ⚡ bolt next to the battery icon.

### 3.2 Optional: Stay Awake (Developer Options)

If you want this in addition to Caffeine:

1. Settings → About tablet → Software information → tap **Build
   number** 7 times to enable Developer Options.
2. Settings → **Developer options** → toggle **Stay awake** on.

On newer Samsung firmware this toggle may be missing or buried;
Caffeine supersedes it either way.

### 3.3 Join Wi-Fi

Settings → Connections → Wi-Fi → join the customer/site network,
enter password, confirm a browser loads google.com. This is the
internet source we'll share to the LAN.

## 4. Enable Ethernet tethering and discover the gateway IP

### 4.1 Enable tethering

1. Plug the USB-ethernet adapter into the tablet's USB-C port.
2. Plug an ethernet cable from the adapter into the switch.
3. Settings → **Connections → Mobile Hotspot and Tethering**.
4. Toggle **Ethernet tethering** on. (The option only appears once
   Android has detected the adapter.)

Android will assign itself an IP on the tethered interface
(typically `192.168.42.129`) and start NATting traffic from
ethernet to wifi.

### 4.2 Discover the gateway IP

Samsung's tethering gateway IP varies by firmware version. Don't
guess — discover it empirically on the first cabinet, then bake
the discovered number into every device's static config.

On any one Pi connected to the switch (orchestrator is fine),
temporarily switch to DHCP:

```bash
sudo dhclient -v eth0
sleep 5
ip route | grep default
cat /etc/resolv.conf | grep nameserver
```

You should see output like:

```
default via 192.168.42.129 dev eth0
nameserver 192.168.42.129
```

The IP after `via` is the **gateway**. Sometimes the DNS Android
hands out is the gateway itself (which forwards DNS), sometimes
it's a public DNS like `8.8.8.8`. Either way, we'll set device
DNS to `1.1.1.1` for predictability.

**Record the discovered gateway IP** — you'll need it for every
device's static config. Most Samsung tablets pick `192.168.42.129`
but verify per device.

If the tablet's firmware picks a different gateway IP, the rest
of this doc still applies — just substitute that IP wherever
`192.168.42.129` appears.

## 5. Per-device static IP configuration

### 5.1 Raspberry Pi (Raspberry Pi OS, dhcpcd)

Edit `/etc/dhcpcd.conf` and append (replacing `101` with the
device's chosen IP from § 2):

```bash
interface eth0
static ip_address=192.168.42.101/24
static routers=192.168.42.129
static domain_name_servers=1.1.1.1 8.8.8.8
```

Reload:

```bash
sudo systemctl restart dhcpcd
```

Verify:

```bash
ip addr show eth0
ip route | grep default
ping -c 3 1.1.1.1   # tests internet via tablet NAT
ping -c 3 192.168.42.102   # tests another LAN device
```

### 5.2 Raspberry Pi (newer Pi OS, NetworkManager)

```bash
sudo nmcli connection modify "Wired connection 1" \
    ipv4.method manual \
    ipv4.addresses 192.168.42.101/24 \
    ipv4.gateway 192.168.42.129 \
    ipv4.dns "1.1.1.1 8.8.8.8"

sudo nmcli connection up "Wired connection 1"
```

### 5.3 Camera / scale / printer

Use the device's own web UI or config tool to set:
- IP: `192.168.42.10x` (per § 2)
- Subnet mask: `255.255.255.0`
- Gateway: `192.168.42.129`
- DNS: `1.1.1.1`

## 6. Day-to-day on-site workflow

When a tech arrives at the cabinet:

1. Plug tablet into charger.
2. Tap the Caffeine notification (screen stays on).
3. Confirm tablet is on wifi (top-right wifi icon visible).
4. Settings → Mobile Hotspot and Tethering → toggle **Ethernet
   tethering** on.
5. SSH from the tablet (or from a laptop also on the cabinet's
   LAN via USB-ethernet) to whichever Pi needs work:
   ```bash
   ssh pi@192.168.42.101    # orchestrator
   ssh pi@192.168.42.103    # vision
   ```
   Or open device web UIs in the tablet's browser:
   `http://192.168.42.104` (camera), etc.
6. Do the work — `sudo apt update && sudo apt upgrade`, edit code,
   pull/push git, etc.
7. When done, toggle Ethernet tethering off. Unplug tablet if
   leaving site.

The LAN keeps working without the tablet — the cabinet's devices
still talk to each other for normal operation. Only the internet
path is gone.

## 7. Known limitations of this approach

| Limitation | Mitigation |
|---|---|
| Tablet must be awake and on wifi for internet to flow | Use Caffeine + wall charger; only run this during on-site sessions |
| Ethernet tethering doesn't auto-enable on reboot | Manual toggle on each session |
| If Samsung firmware update changes the gateway IP, internet breaks until re-discovered | Re-run § 4.2 once after each major Samsung update; update device configs |
| No remote ssh from off-site (Tailscale needs continuous internet on the Pi) | Out of scope for this doc — needs the GL.iNet router approach (future work) |
| Customer Wi-Fi with WPA2-Enterprise / captive portal will not work | Use a cellular hotspot tablet plan or 4G dongle as fallback |

## 8. Future: 24/7 internet path

When we move past prototyping, replace the tablet-as-gateway with
either:

- **GL.iNet travel router** mounted outside the cabinet (joins
  customer wifi, ethernet uplink to switch, runs Tailscale for
  remote access)
- **GL.iNet 4G router** for sites where customer wifi is locked
  down

Either approach gives the cabinet a 24/7 internet path and enables
remote `ssh` from anywhere via Tailscale, which the tablet-only
approach cannot.

This doc stays as the procedure for **prototyping and on-site
investigation** even after we adopt a permanent gateway, since
the tablet remains a useful fallback when the permanent gateway
is the thing being debugged.
