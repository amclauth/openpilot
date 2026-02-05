#!/usr/bin/env python3
"""OBD-II DTC scanner daemon.

Runs after ignition off to scan ECUs for diagnostic trouble codes via
UDS over ISO-TP. Results are written as JSON to the last segment directory
of the most recent route.

Requires pandad.cc modification that uses ELM327 safety mode (instead of
NO_OUTPUT) when ignition is off, keeping CAN transceivers alive and
enabling OBD-II diagnostic TX on bus 1.
"""

import glob
import json
import logging
import signal
import time
from pathlib import Path

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.selfdrive.pandad import can_list_to_can_capnp
from openpilot.system.hardware.hw import Paths
from panda.python.uds import get_dtc_num_as_str, get_dtc_status_names

logger = logging.getLogger("dtc_scannerd")

OBD_BUS = 1
BROADCAST_TX = 0x7DF
RESPONSE_TIMEOUT_S = 2.0
SETTLE_DELAY_S = 5.0

# Mazda CX-30 ECU physical TX addresses (response at tx + 8)
KNOWN_ECUS = {
    0x700: "unknown-700",
    0x706: "unknown-706",
    0x710: "unknown-710",
    0x720: "Camera/MRCC",
    0x730: "unknown-730",
    0x738: "unknown-738",
    0x740: "unknown-740",
    0x748: "unknown-748",
    0x750: "BCM",
    0x760: "IC",
    0x764: "unknown-764",
    0x768: "unknown-768",
    0x770: "unknown-770",
    0x780: "unknown-780",
    0x790: "unknown-790",
    0x7C0: "unknown-7C0",
    0x7C4: "unknown-7C4",
    0x7CC: "unknown-7CC",
    0x7E0: "PCM",
    0x7E1: "TCM",
    0x7E2: "unknown-7E2",
}

_shutdown = False


def _signal_handler(_signum: int, _frame: object) -> None:
    global _shutdown
    _shutdown = True


def ecu_name(addr: int) -> str:
    """Get human-readable name for an ECU address."""
    return KNOWN_ECUS.get(addr, f"unknown-0x{addr:03X}")


def send_can(
    sendcan: messaging.PubSocket, addr: int, data: bytes, bus: int
) -> None:
    """Send a CAN message via the sendcan socket."""
    msg = [addr, 0, data, bus]
    sendcan.send(can_list_to_can_capnp([msg], msgtype="sendcan"))


def collect_responses(
    logcan: messaging.SubSocket, bus: int, timeout_s: float = RESPONSE_TIMEOUT_S
) -> list[tuple[int, bytes]]:
    """Collect CAN responses on the specified bus within timeout."""
    responses = []
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if _shutdown:
            break
        packets = messaging.drain_sock(logcan, wait_for_one=False)
        for pkt in packets:
            for msg in pkt.can:
                if msg.src == bus:
                    responses.append((msg.address, bytes(msg.dat)))
        if not packets:
            time.sleep(0.01)
    return responses


def make_isotp_single(uds_payload: bytes) -> bytes:
    """Build a single-frame ISO-TP message (max 7 bytes UDS payload)."""
    n = len(uds_payload)
    if n > 7:
        raise ValueError(f"UDS payload too long for single frame: {n} bytes")
    return bytes([n]) + uds_payload + b"\x00" * (7 - n)


def parse_isotp_single(data: bytes) -> bytes | None:
    """Parse a single-frame ISO-TP response. Returns UDS payload or None."""
    if len(data) < 1:
        return None
    frame_type = (data[0] >> 4) & 0x0F
    if frame_type == 0:  # Single frame
        length = data[0] & 0x0F
        if length == 0 or length > 7:
            return None
        return data[1 : 1 + length]
    if frame_type == 1:  # First frame of multi-frame (partial)
        logger.debug("Multi-frame response detected, returning first 6 bytes")
        return data[2:8]
    return None


def find_last_segment_dir(route: str) -> Path | None:
    """Find the last segment directory for a route."""
    log_root = Paths.log_root()
    pattern = f"{log_root}/{route}--*"
    segments = sorted(glob.glob(pattern))
    if not segments:
        logger.warning("No segment directories found for route: %s", route)
        return None
    return Path(segments[-1])


def discover_ecus(
    sendcan: messaging.PubSocket, logcan: messaging.SubSocket
) -> set[int]:
    """Broadcast TesterPresent and return set of responding RX addresses."""
    messaging.drain_sock(logcan)
    time.sleep(0.1)
    messaging.drain_sock(logcan)

    logger.info("TesterPresent broadcast on bus %d", OBD_BUS)
    send_can(sendcan, BROADCAST_TX, make_isotp_single(b"\x3E\x00"), OBD_BUS)
    responses = collect_responses(logcan, OBD_BUS)

    alive = set()
    for addr, data in responses:
        uds = parse_isotp_single(data)
        if uds is None:
            continue
        if uds[0] in (0x7E, 0x7F):  # Positive or negative (ECU is alive)
            alive.add(addr)
            logger.info("  ECU alive: 0x%03X (%s)", addr, ecu_name(addr))

    logger.info("%d ECU(s) responded to TesterPresent", len(alive))
    return alive


def enter_extended_session(
    sendcan: messaging.PubSocket, logcan: messaging.SubSocket
) -> None:
    """Broadcast extended diagnostic session request."""
    messaging.drain_sock(logcan)
    time.sleep(0.1)
    logger.info("Requesting extended diagnostic session")
    send_can(sendcan, BROADCAST_TX, make_isotp_single(b"\x10\x03"), OBD_BUS)
    time.sleep(0.5)
    # Drain session responses
    collect_responses(logcan, OBD_BUS, timeout_s=1.0)


def read_dtcs(
    sendcan: messaging.PubSocket,
    logcan: messaging.SubSocket,
    alive_addrs: set[int],
) -> dict[str, dict]:
    """Read DTCs from each alive ECU. Returns results keyed by address string."""
    results = {}

    for rx_addr in sorted(alive_addrs):
        if _shutdown:
            break

        tx_addr = rx_addr - 8
        name = ecu_name(tx_addr)
        addr_key = f"0x{tx_addr:03X}"

        messaging.drain_sock(logcan)
        time.sleep(0.1)

        logger.info("DTC query: 0x%03X (%s)", tx_addr, name)
        send_can(
            sendcan, tx_addr, make_isotp_single(b"\x19\x02\xFF"), OBD_BUS
        )
        dtc_responses = collect_responses(logcan, OBD_BUS, timeout_s=3.0)

        ecu_result = {"name": name, "address": addr_key, "dtcs": [], "error": None}

        got_response = False
        for addr, data in dtc_responses:
            if addr != rx_addr:
                continue
            got_response = True
            uds = parse_isotp_single(data)
            if uds is None:
                continue

            if uds[0] == 0x59:  # Positive ReadDTCInformation response
                i = 2  # skip sub-function and status availability mask
                while i + 3 < len(uds):
                    dtc_str = get_dtc_num_as_str(uds[i : i + 3])
                    dtc_status = uds[i + 3]
                    status_names = get_dtc_status_names(dtc_status)
                    ecu_result["dtcs"].append({
                        "code": dtc_str,
                        "status": status_names,
                    })
                    logger.info(
                        "  DTC: %s [%s]", dtc_str, " ".join(status_names)
                    )
                    i += 4
                if not ecu_result["dtcs"]:
                    logger.info("  No DTCs stored")
            elif uds[0] == 0x7F:  # Negative response
                nrc = uds[2] if len(uds) > 2 else 0xFF
                ecu_result["error"] = f"NRC 0x{nrc:02X}"
                logger.info("  DTC query rejected: NRC 0x%02X", nrc)

        if not got_response:
            ecu_result["error"] = "timeout"
            logger.info("  No response to DTC query")

        results[addr_key] = ecu_result

    return results


def main() -> None:
    """Entry point for the DTC scanner daemon."""
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    params = Params()
    logger.info("dtc_scannerd starting, waiting %ds for pandad ELM327 mode",
                int(SETTLE_DELAY_S))

    # Wait for pandad to settle into ELM327 mode after ignition off
    for _ in range(int(SETTLE_DELAY_S * 10)):
        if _shutdown:
            logger.info("Shutdown requested during settle wait")
            return
        time.sleep(0.1)

    # Find the last route's segment directory for output
    current_route = params.get("CurrentRoute")
    output_dir = None
    if current_route:
        route_str = current_route.decode("utf-8").strip()
        if route_str:
            output_dir = find_last_segment_dir(route_str)
            if output_dir:
                logger.info("Output directory: %s", output_dir)

    if output_dir is None:
        logger.warning("No route directory found, will write to log root")
        output_dir = Path(Paths.log_root())

    # Set up cereal messaging
    sendcan = messaging.pub_sock("sendcan")
    logcan = messaging.sub_sock("can", conflate=False, timeout=1000)
    time.sleep(1.0)  # Allow sockets to connect

    if _shutdown:
        return

    # Discover alive ECUs
    alive_addrs = discover_ecus(sendcan, logcan)
    if not alive_addrs and not _shutdown:
        logger.info("No ECUs responded, retrying once after 2s")
        time.sleep(2.0)
        alive_addrs = discover_ecus(sendcan, logcan)

    scan_result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ecus_discovered": len(alive_addrs),
        "ecus": {},
    }

    if alive_addrs and not _shutdown:
        enter_extended_session(sendcan, logcan)
        scan_result["ecus"] = read_dtcs(sendcan, logcan, alive_addrs)

    # Count total DTCs
    total_dtcs = sum(
        len(ecu["dtcs"]) for ecu in scan_result["ecus"].values()
    )
    scan_result["total_dtcs"] = total_dtcs

    # Write results
    output_path = output_dir / "dtc_scan.json"
    try:
        with open(output_path, "w") as f:
            json.dump(scan_result, f, indent=2)
        logger.info("Results written to %s", output_path)
    except OSError:
        logger.exception("Failed to write results to %s", output_path)

    # Signal completion
    params.put("DtcScanComplete", "1")
    logger.info(
        "Scan complete: %d ECUs, %d DTCs", len(alive_addrs), total_dtcs
    )


if __name__ == "__main__":
    main()
