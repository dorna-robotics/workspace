"""HTTPS for the Workspace — so a browser on another device on the network
gets a secure page (the AI's microphone needs one; plain http is only
"secure" on localhost).

* ``ssl_context()`` — a self-signed certificate in ~/.workspace/tls/
  (cert.pem, key.pem 0600) naming localhost, 127.0.0.1, this robot's
  hostname and hostname.local and its LAN addresses; made on first start
  and again when the addresses change. Through the ``cryptography``
  package, else the ``openssl`` command; None when neither works.
* ``ViewerTLS`` — a launched project's own server (main.py, plain http on
  its port) can't be framed or fetched from an https page (mixed content),
  so each local project's port P also answers https on P + OFFSET here: TLS
  ends in this process and the bytes go on to 127.0.0.1:P unchanged (its
  pages, /ws and socket.io keep their absolute paths).
"""
import datetime
import ipaddress
import json
import logging
import os
import socket
import ssl
import subprocess
import tempfile

import tornado.ioloop
import tornado.iostream
import tornado.tcpclient
import tornado.tcpserver

log = logging.getLogger("gui.tls")

OFFSET = 443                       # https port = http port + 443 (5000 → 5443)
TLS_DIR = os.path.expanduser("~/.workspace/tls")
CERT, KEY, NAMES = (os.path.join(TLS_DIR, n) for n in ("cert.pem", "key.pem", "names.json"))


def _names():
    host = socket.gethostname()
    dns = sorted({"localhost", host, host + ".local"})
    ips = {"127.0.0.1"}
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout
        ips.update(a for a in out.split() if ":" not in a)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return {"dns": dns, "ip": sorted(ips)}


def _make_cryptography(names):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Dorna Workspace ({names['dns'][0]})")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName(d) for d in names["dns"]] + [x509.IPAddress(ipaddress.ip_address(i)) for i in names["ip"]]
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def _make_openssl(names):
    san = ",".join([f"DNS:{d}" for d in names["dns"]] + [f"IP:{i}" for i in names["ip"]])
    with tempfile.TemporaryDirectory() as tmp:
        k, c = os.path.join(tmp, "k"), os.path.join(tmp, "c")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-days", "825", "-subj", f"/CN=Dorna Workspace ({names['dns'][0]})",
                        "-addext", f"subjectAltName={san}", "-keyout", k, "-out", c],
                       check=True, capture_output=True, timeout=60)
        with open(c, "rb") as f1, open(k, "rb") as f2:
            return f1.read(), f2.read()


def _write(path, data, mode):
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(path + ".tmp", mode)
    os.replace(path + ".tmp", path)


def ensure_cert():
    """The cert and key paths, made or remade as needed; None on failure."""
    names = _names()
    try:
        with open(NAMES) as f:
            same = json.load(f) == names
    except (OSError, ValueError):
        same = False
    if same and os.path.isfile(CERT) and os.path.isfile(KEY):
        return CERT, KEY
    os.makedirs(TLS_DIR, mode=0o700, exist_ok=True)
    for make in (_make_cryptography, _make_openssl):
        try:
            cert, key = make(names)
            break
        except Exception as e:                 # ImportError, no openssl, a failed run
            log.info("tls: %s didn't make a certificate: %s", make.__name__, e)
    else:
        return None
    _write(KEY, key, 0o600)
    _write(CERT, cert, 0o644)
    _write(NAMES, json.dumps(names).encode(), 0o644)
    return CERT, KEY


def ssl_context():
    paths = ensure_cert()
    if not paths:
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(*paths)
    return ctx


# ── https in front of each local project's own server ──────────────────

class _Forward(tornado.tcpserver.TCPServer):
    def __init__(self, target, ctx):
        super().__init__(ssl_options=ctx)
        self.target = target

    async def handle_stream(self, stream, address):
        try:
            up = await tornado.tcpclient.TCPClient().connect("127.0.0.1", self.target, timeout=5)
        except Exception:
            stream.close()            # the project isn't running
            return
        up.set_nodelay(True)
        stream.set_nodelay(True)

        async def pipe(a, b):
            try:
                while True:
                    b.write(await a.read_bytes(65536, partial=True))
            except (tornado.iostream.StreamClosedError, OSError):
                pass
            finally:
                a.close()
                b.close()
        tornado.ioloop.IOLoop.current().add_callback(pipe, stream, up)
        await pipe(up, stream)


class ViewerTLS:
    """Keeps an https listener on P + OFFSET for each local project port P."""

    def __init__(self, orch, ctx, host="0.0.0.0", every_s=3):
        self.orch, self.ctx, self.host = orch, ctx, host
        self.servers = {}             # project port → _Forward
        self.failed = set()
        tornado.ioloop.PeriodicCallback(self.sync, every_s * 1000).start()
        self.sync()

    def sync(self):
        want = {int(ws.port) for ws in list(self.orch.workspaces.values())
                if not ws.node_url and ws.port}
        for p in set(self.servers) - want:
            self.servers.pop(p).stop()
        for p in want - set(self.servers) - self.failed:
            srv = _Forward(p, self.ctx)
            try:
                srv.listen(p + OFFSET, address=self.host)
            except OSError as e:
                self.failed.add(p)
                log.warning("tls: can't serve https for port %s on %s: %s", p, p + OFFSET, e)
                continue
            self.servers[p] = srv
