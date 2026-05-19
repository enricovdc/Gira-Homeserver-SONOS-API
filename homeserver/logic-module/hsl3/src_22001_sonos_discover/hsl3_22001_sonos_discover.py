"""LBS 22001 - Sonos Discover.

SSDP M-SEARCH on the LAN. Emits a newline-separated list of discovered
Sonos players on the Result output: ``ip;uuid;model`` per line. Use it
once at commissioning to populate the Host input of each Sonos Player
node.
"""

import re
import socket
import threading
import time

import requests


SSDP_HOST = "239.255.255.250"
SSDP_PORT = 1900
SEARCH_TARGETS = [
    "urn:schemas-upnp-org:device:ZonePlayer:1",
    "urn:smartspeaker-audio:service:SpeakerGroup:1",
    "ssdp:all",
]


def looks_like_sonos(headers):
    server = headers.get("SERVER", "").lower()
    usn = headers.get("USN", "").lower()
    return ("sonos" in server) or ("rincon" in usn) or ("zoneplayer" in usn)


def parse_ssdp_response(raw):
    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().upper()] = v.strip()
    return headers


def fetch_model(location, timeout):
    try:
        resp = requests.get(location, timeout=timeout)
        m = re.search(r"<modelName>([^<]+)</modelName>", resp.text)
        return m.group(1) if m else ""
    except Exception:
        return ""


def discover(timeout_sec):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)

    try:
        for target in SEARCH_TARGETS:
            msg = (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: {}:{}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                "ST: {}\r\n\r\n"
            ).format(SSDP_HOST, SSDP_PORT, target).encode("utf-8")
            try:
                sock.sendto(msg, (SSDP_HOST, SSDP_PORT))
            except OSError:
                pass

        deadline = time.time() + timeout_sec
        found = {}
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            headers = parse_ssdp_response(data)
            if not looks_like_sonos(headers):
                continue
            ip = addr[0]
            if ip in found:
                continue
            usn = headers.get("USN", "")
            m = re.search(r"uuid:([A-Za-z0-9_-]+)", usn)
            uuid = m.group(1) if m else ""
            location = headers.get("LOCATION", "")
            model = fetch_model(location, 2) if location else ""
            found[ip] = (ip, uuid, model)
        return list(found.values())
    finally:
        try:
            sock.close()
        except Exception:
            pass


class LogicModule:

    def __init__(self, hsl3):
        self.fw = hsl3
        self.debug = None

    def on_init(self, inputs, store):
        self.debug = self.fw.create_debug_section()
        self.debug.set("Discovered", 0)
        self.debug.set("Last run", "-")

    def on_calc(self, inputs):
        trigger = inputs["Trigger"].value
        if not (inputs["Trigger"].changed and trigger):
            return
        timeout = int(inputs["Timeout"].value or 4)
        t = threading.Thread(target=self._run_discover, args=(timeout,), daemon=True)
        t.start()

    def _run_discover(self, timeout):
        try:
            players = discover(timeout)
        except Exception as e:
            self.fw.run_in_context(self._handle_error, ("DISCOVER_FAILED: {}".format(e),))
            return
        self.fw.run_in_context(self._handle_result, (players,))

    def _handle_result(self, players):
        if self.debug is not None:
            self.debug.set("Discovered", float(len(players)))
            self.debug.timestamp("Last run")
        text = "\n".join("{};{};{}".format(ip, uuid, model)
                         for (ip, uuid, model) in players)
        self.fw.set_output("Result", text.encode("iso-8859-15", "replace"))
        self.fw.set_output("Count", float(len(players)))
        self.fw.set_output("Error", "".encode("iso-8859-15"))

    def _handle_error(self, msg):
        if self.debug is not None:
            self.debug.set("Last error", msg.encode("iso-8859-15", "replace"))
        self.fw.set_output("Error", msg.encode("iso-8859-15", "replace"))
