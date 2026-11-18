"""Application-level NTP connectivity probe and status monitor.

The dashboard needs to know whether *this machine* can reach the internet,
because that is what the ESP32 clocks depend on for their SNTP sync.  A
minimal SNTP client (stdlib UDP only — no new dependency) queries the
configured servers from a worker thread; the result is advisory and shown in
the UI.  It is deliberately independent from record-timestamp correction
(:mod:`app.timestamps`): a probe failure must never rewrite data, and an
inaccurate firmware clock must be corrected even when the host is online.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass

from app.timestamps import TIMESTAMP_MIN_VALID_EPOCH

logger = logging.getLogger("smokeguard.ntp")

# Seconds between the NTP era (1900-01-01) and the UNIX epoch (1970-01-01).
NTP_EPOCH_DELTA = 2_208_988_800
NTP_PACKET_SIZE = 48
_NTP_PORT = 123

# NTPv4, client mode: LI=0, VN=4, Mode=3.
_NTP_REQUEST_FIRST_BYTE = 0x23


class NtpError(Exception):
    """A concise, UI-safe probe failure."""


@dataclass(frozen=True)
class NtpProbe:
    """A successful server probe."""

    server: str
    offset_s: float  # server time minus host time (diagnostic only)
    rtt_s: float


# ---------------------------------------------------------------------------
# SNTP wire protocol
# ---------------------------------------------------------------------------

def build_request() -> bytes:
    """Build a 48-byte NTPv4 client request."""
    return bytes([_NTP_REQUEST_FIRST_BYTE]) + bytes(NTP_PACKET_SIZE - 1)


def parse_transmit_time(data: bytes) -> float:
    """Parse the transmit timestamp of an NTP server response (UNIX epoch).

    Raises :class:`NtpError` for malformed/unusable replies rather than
    returning a bogus time.
    """
    if len(data) < NTP_PACKET_SIZE:
        raise NtpError("short NTP response")
    first = data[0]
    mode = first & 0x07
    version = (first >> 3) & 0x07
    stratum = data[1]
    if mode != 4:
        raise NtpError(f"unexpected NTP mode {mode}")
    if version not in (3, 4):
        raise NtpError(f"unsupported NTP version {version}")
    if not 1 <= stratum <= 15:
        # Stratum 0 is a kiss-o'-death; 16 means unsynchronized.
        raise NtpError(f"unusable NTP stratum {stratum}")

    seconds, fraction = struct.unpack("!II", data[40:48])
    if seconds == 0 and fraction == 0:
        raise NtpError("empty NTP transmit timestamp")
    timestamp = seconds + fraction / 2**32 - NTP_EPOCH_DELTA
    if timestamp < TIMESTAMP_MIN_VALID_EPOCH:
        raise NtpError("NTP server time is implausible")
    return timestamp


def compute_offset_rtt(
    server_time: float, t1: float, t2: float
) -> tuple[float, float]:
    """Approximate offset and round-trip time from one SNTP exchange.

    Without the server-receive/transmit pair this uses the local send/receive
    times: ``offset = server_time - (t1 + t2) / 2``.  It is diagnostic only.
    """
    return server_time - (t1 + t2) / 2.0, t2 - t1


# ---------------------------------------------------------------------------
# DNS + UDP probe
# ---------------------------------------------------------------------------

def _resolve_addresses(host: str, port: int, timeout_s: float) -> list:
    """Resolve ``host`` with a hard deadline (getaddrinfo has no timeout)."""
    result: dict[str, object] = {}

    def run() -> None:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
            result["addrs"] = list(
                dict.fromkeys(info[4] for info in infos)  # de-duplicate
            )
        except Exception as exc:  # surfaced to the caller below
            result["error"] = exc

    thread = threading.Thread(
        target=run, daemon=True, name="ntp-resolve"
    )
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise NtpError("DNS resolution timed out")
    error = result.get("error")
    if error is not None:
        raise NtpError(f"DNS resolution failed: {error}")
    addrs = result.get("addrs") or []
    if not addrs:
        raise NtpError("no usable address")
    # Prefer IPv4 so a broken IPv6 path cannot consume the whole timeout.
    return sorted(addrs, key=lambda addr: 0 if len(addr) == 2 else 1)


def probe_server(server: str, timeout_s: float) -> NtpProbe:
    """Query one NTP server, returning its offset/RTT or raising NtpError."""
    deadline = time.monotonic() + timeout_s
    addresses = _resolve_addresses(server, _NTP_PORT, timeout_s)
    last_error: NtpError | None = None

    for sockaddr in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        family = socket.AF_INET6 if len(sockaddr) == 4 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            sock.settimeout(remaining)
            sent_at = time.time()
            sock.sendto(build_request(), sockaddr)
            data, _ = sock.recvfrom(512)
            received_at = time.time()
        except TimeoutError:
            last_error = NtpError("timed out")
            continue
        except OSError as exc:
            # Includes ICMP port-unreachable surfaced as ConnectionRefused.
            last_error = NtpError(str(exc) or exc.__class__.__name__)
            continue
        finally:
            sock.close()

        server_time = parse_transmit_time(data)
        offset, rtt = compute_offset_rtt(server_time, sent_at, received_at)
        return NtpProbe(server=server, offset_s=offset, rtt_s=rtt)

    raise last_error or NtpError("timed out")


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class NtpMonitor:
    """Thread-safe connectivity state with async single-flight probing."""

    def __init__(
        self,
        servers: list[str],
        timeout_s: float,
        recheck_interval_s: float = 300.0,
    ) -> None:
        self._servers = list(servers)
        self._timeout_s = timeout_s
        self._recheck_interval_s = recheck_interval_s

        self._lock = threading.Lock()
        self._state = "unknown"
        self._server: str | None = None
        self._offset_s: float | None = None
        self._rtt_s: float | None = None
        self._last_checked_at: float | None = None
        self._error: str | None = None

        # Coalesces concurrent refresh() calls onto one probe.  Created
        # lazily inside refresh() so constructing a monitor never needs a
        # running event loop.
        self._inflight: asyncio.Task[dict] | None = None

    # -- public read API ---------------------------------------------------

    def snapshot(self) -> dict:
        """Return a thread-safe copy of the current probe state."""
        with self._lock:
            return {
                "state": self._state,
                "server": self._server,
                "offset_s": self._offset_s,
                "rtt_s": self._rtt_s,
                "last_checked_at": self._last_checked_at,
                "error": self._error,
            }

    # -- probing -----------------------------------------------------------

    async def refresh(self) -> dict:
        """Probe now (or join an in-flight probe) and return the snapshot.

        Never raises for probe failures — the state becomes ``offline`` with
        an error message.  Cancellation (shutdown) is propagated.
        """
        task = self._inflight
        if task is None:
            task = asyncio.create_task(self._run_check(), name="ntp-check")
            self._inflight = task
        try:
            return await task
        finally:
            if self._inflight is task:
                self._inflight = None

    async def run_periodic(self) -> None:
        """Check immediately at startup, then on the configured interval."""
        await self.refresh()
        if self._recheck_interval_s <= 0:
            return
        while True:
            await asyncio.sleep(self._recheck_interval_s)
            await self.refresh()

    async def _run_check(self) -> dict:
        if not self._servers:
            self._finish_offline("no NTP servers configured")
            return self.snapshot()

        with self._lock:
            if self._state == "unknown":
                self._state = "checking"

        try:
            probe = await asyncio.to_thread(self._probe_all)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._finish_offline(str(exc) or exc.__class__.__name__)
            return self.snapshot()

        self._finish_online(probe)
        return self.snapshot()

    def _probe_all(self) -> NtpProbe:
        errors: list[str] = []
        for server in self._servers:
            try:
                return probe_server(server, self._timeout_s)
            except NtpError as exc:
                errors.append(f"{server}: {exc}")
            except Exception as exc:  # never let one bad server kill the check
                errors.append(f"{server}: {exc.__class__.__name__}: {exc}")
        raise NtpError("; ".join(errors) or "all NTP servers failed")

    def _finish_online(self, probe: NtpProbe) -> None:
        with self._lock:
            previous = self._state
            self._state = "online"
            self._server = probe.server
            self._offset_s = probe.offset_s
            self._rtt_s = probe.rtt_s
            self._last_checked_at = time.time()
            self._error = None
        log = logger.info if previous != "online" else logger.debug
        log(
            "Internet NTP online via %s (offset %+.3f s, rtt %.1f ms)",
            probe.server, probe.offset_s, probe.rtt_s * 1000.0,
        )

    def _finish_offline(self, error: str) -> None:
        with self._lock:
            previous = self._state
            self._state = "offline"
            self._server = None
            self._offset_s = None
            self._rtt_s = None
            self._last_checked_at = time.time()
            self._error = error
        log = logger.warning if previous != "offline" else logger.debug
        log("Internet NTP check failed: %s", error)
