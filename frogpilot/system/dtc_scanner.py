"""Boot-time OBD-II DTC scanner.

Standalone module (not a daemon). Runs during manager_init() before pandad
starts, when ignition is on and all ECUs are awake. Uses the panda's USB
interface directly with UdsClient for proper ISO-TP handling.

Only DTCs with actionable failure status are persisted (confirmed, failed,
or warning indicator). DTCs that are merely "test not completed" are
discarded to reduce noise.

Results are cached in /data/dtc_scan.json and optionally copied to segment 0
of the current route once driving begins.
"""

import json
import logging
import os
import shutil
import signal
import time
from pathlib import Path

from panda import Panda
from panda.python.uds import (
    UdsClient,
    SESSION_TYPE,
    DTC_REPORT_TYPE,
    DTC_STATUS_MASK_TYPE,
    MessageTimeoutError,
    NegativeResponseError,
    get_dtc_num_as_str,
    get_dtc_status_names,
)

from openpilot.common.params import Params
from openpilot.common.spinner import Spinner
from openpilot.system.hardware.hw import Paths

logger = logging.getLogger("dtc_scanner")

SCAN_TIMEOUT_S = 30
DISCOVERY_WAIT_S = 2.0
DTC_RESULT_PATH = "/data/dtc_scan.json"
TMP_RESULT_PATH = "/tmp/dtc_scan.json"

# DTC status bits that indicate an actual fault worth persisting.
# Everything else (TEST_NOT_COMPLETED_*, PENDING_DTC alone) is noise.
ACTIONABLE_STATUS = {
    "CONFIRMED_DTC",
    "TEST_FAILED_THIS_OPERATION_CYCLE",
    "TEST_FAILED_SINCE_LAST_CLEAR",
    "WARNING_INDICATOR_REQUESTED",
}

# NRC 0x7F = serviceNotSupportedInActiveSession
NRC_SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F

# Standard OBD-II ECU TX addresses and names
KNOWN_ECU_NAMES = {
    0x700: "unknown-0x700",
    0x706: "unknown-0x706",
    0x710: "unknown-0x710",
    0x720: "Camera/ADAS",
    0x730: "unknown-0x730",
    0x738: "unknown-0x738",
    0x740: "unknown-0x740",
    0x748: "unknown-0x748",
    0x750: "BCM",
    0x760: "IC",
    0x764: "unknown-0x764",
    0x768: "unknown-0x768",
    0x770: "unknown-0x770",
    0x780: "unknown-0x780",
    0x790: "unknown-0x790",
    0x7C0: "unknown-0x7C0",
    0x7C4: "unknown-0x7C4",
    0x7CC: "unknown-0x7CC",
    0x7E0: "PCM",
    0x7E1: "TCM",
    0x7E2: "SRS/Airbag",
}

_timed_out = False


def _alarm_handler(_signum: int, _frame: object) -> None:
    global _timed_out
    _timed_out = True


def ecu_name(tx_addr: int) -> str:
    """Get human-readable name for an ECU TX address."""
    return KNOWN_ECU_NAMES.get(tx_addr, f"unknown-0x{tx_addr:03X}")


def discover_ecus(panda: Panda, bus: int) -> list[int]:
    """Broadcast TesterPresent and return list of responding TX addresses.

    Sends a broadcast TesterPresent (0x7DF) via raw CAN and collects
    responses for DISCOVERY_WAIT_S seconds. Each response at rx_addr
    implies tx_addr = rx_addr - 8.

    Returns:
        List of TX addresses (e.g. 0x7E0) that responded.
    """
    # Flush any stale CAN data
    panda.can_clear(bus)
    time.sleep(0.1)
    panda.can_clear(bus)

    logger.info("TesterPresent broadcast on bus %d", bus)
    panda.can_send(0x7DF, b"\x02\x3E\x00\x00\x00\x00\x00\x00", bus)

    tx_addrs = set()
    start = time.monotonic()
    while time.monotonic() - start < DISCOVERY_WAIT_S:
        if _timed_out:
            break
        msgs = panda.can_recv()
        for addr, _, dat, msg_bus in msgs:
            if msg_bus != bus:
                continue
            if len(dat) < 2:
                continue
            # Check for positive response (0x7E) or negative (0x7F)
            pci_len = dat[0] & 0x0F
            if pci_len >= 2 and dat[1] in (0x7E, 0x7F):
                tx_addr = addr - 8
                tx_addrs.add(tx_addr)
                logger.info("  ECU alive: 0x%03X (%s)", tx_addr, ecu_name(tx_addr))
        time.sleep(0.01)

    logger.info("%d ECU(s) responded to TesterPresent", len(tx_addrs))
    return sorted(tx_addrs)


def _read_dtcs(uds: UdsClient, name: str) -> list[dict]:
    """Issue a ReadDTCInformation request and parse the response.

    Returns list of all DTC dicts (unfiltered).
    """
    data = uds.read_dtc_information(
        DTC_REPORT_TYPE.DTC_BY_STATUS_MASK,
        DTC_STATUS_MASK_TYPE.ALL,
    )

    if data is None or len(data) < 1:
        logger.info("  %s: no DTC data returned", name)
        return []

    dtcs: list[dict] = []
    # data[0] = status availability mask
    # data[1:] = 4-byte records: 3-byte DTC + 1-byte status
    i = 1
    while i + 3 <= len(data):
        dtc_bytes = data[i:i + 3]
        dtc_status = data[i + 3]
        dtc_str = get_dtc_num_as_str(dtc_bytes)
        status_names = get_dtc_status_names(dtc_status)
        dtcs.append({"code": dtc_str, "status": status_names})
        logger.info("  %s: DTC %s [%s]", name, dtc_str, " ".join(status_names))
        i += 4

    return dtcs


def scan_ecu_dtcs(panda: Panda, tx_addr: int, bus: int) -> dict:
    """Query a single ECU for DTCs using UdsClient.

    Tries to read DTCs in the default session first (non-disruptive).
    Falls back to extended diagnostic session only if the ECU rejects
    the request with NRC 0x7F (serviceNotSupportedInActiveSession).

    Returns:
        Dict with keys: name, address, dtcs, error, supports_dtc
    """
    name = ecu_name(tx_addr)
    addr_key = f"0x{tx_addr:03X}"
    result = {
        "name": name,
        "address": addr_key,
        "dtcs": [],
        "error": None,
        "supports_dtc": True,
    }

    try:
        uds = UdsClient(panda, tx_addr, bus=bus, timeout=1.0)

        try:
            all_dtcs = _read_dtcs(uds, name)
        except NegativeResponseError as e:
            if e.error_code == NRC_SERVICE_NOT_SUPPORTED_IN_SESSION:
                logger.info("  %s: default session rejected, trying extended", name)
                uds.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
                all_dtcs = _read_dtcs(uds, name)
            else:
                raise

        # Filter: only keep DTCs with actionable failure status
        for dtc in all_dtcs:
            if ACTIONABLE_STATUS.intersection(dtc["status"]):
                result["dtcs"].append(dtc)

        kept = len(result["dtcs"])
        skipped = len(all_dtcs) - kept
        if skipped:
            logger.info("  %s: %d DTCs kept, %d filtered (test-not-completed)", name, kept, skipped)
        elif not all_dtcs:
            logger.info("  %s: no DTCs stored", name)

    except MessageTimeoutError:
        result["error"] = "timeout"
        logger.info("  %s: timeout", name)

    except NegativeResponseError as e:
        nrc = f"0x{e.error_code:02X}"
        result["error"] = f"NRC {nrc}"
        result["supports_dtc"] = False
        logger.info("  %s: DTC query rejected: NRC %s", name, nrc)

    except Exception:
        logger.exception("  %s: unexpected error", name)
        result["error"] = "exception"

    return result


def run_boot_scan(show_spinner: bool = True) -> None:
    """Main entry point for boot-time DTC scan.

    Reads/writes DtcEcuMap param for ECU caching. Results go to
    /tmp/dtc_scan.json (copied to /data later) and /data/dtc_scan.json.
    """
    global _timed_out
    _timed_out = False

    params = Params()

    # Set hard timeout to never block boot
    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(SCAN_TIMEOUT_S)

    try:
        _run_scan(params, show_spinner)
    except Exception:
        logger.exception("DTC boot scan failed")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _run_scan(params: Params, show_spinner: bool) -> None:
    """Internal scan implementation."""
    global _timed_out

    spinner = None
    panda = None

    try:
        # Open panda first for ignition check (covers both paths)
        panda = Panda()
        panda.set_safety_mode(Panda.SAFETY_ELM327)

        # Skip scan if car is not on (USB power only)
        health = panda.health()
        if not health.get("ignition_line", False) and not health.get("ignition_can", False):
            logger.info("Ignition off, skipping DTC scan")
            return

        # Load ECU cache
        cache_raw = params.get("DtcEcuMap")
        ecu_cache = json.loads(cache_raw) if cache_raw else None

        if ecu_cache:
            cached_bus = ecu_cache.get("bus", 0)
            cached_ecus = ecu_cache.get("ecus", {})
            # Filter to DTC-capable ECUs only
            scan_addrs = [
                int(addr, 16) for addr, info in cached_ecus.items()
                if info.get("supports_dtc", True)
            ]
            obd_bus = cached_bus
            logger.info(
                "Using cached ECU map: %d DTC-capable ECUs on bus %d",
                len(scan_addrs), obd_bus
            )
            if show_spinner:
                spinner = Spinner()
                spinner.update("Scanning DTCs...")
        else:
            # Full discovery
            if show_spinner:
                spinner = Spinner()
                spinner.update("Scanning vehicle ECUs...")

            # Try bus 0 first (where OBD-II is typically muxed)
            obd_bus = 1 if panda.has_obd() else 0
            scan_addrs = discover_ecus(panda, obd_bus)

            if not scan_addrs and not _timed_out:
                # Try the other bus
                alt_bus = 0 if obd_bus == 1 else 1
                logger.info("No responses on bus %d, trying bus %d", obd_bus, alt_bus)
                scan_addrs = discover_ecus(panda, alt_bus)
                if scan_addrs:
                    obd_bus = alt_bus

            if not scan_addrs:
                logger.warning("No ECUs responded on any bus")
                _save_results({
                    "ecus_discovered": 0,
                    "ecus": {},
                    "total_dtcs": 0,
                })
                return

        # Scan each ECU for DTCs
        scan_result = {
            "ecus_discovered": len(scan_addrs),
            "ecus": {},
            "total_dtcs": 0,
        }

        # Build cache data (only on first discovery)
        cache_data = {
            "bus": obd_bus,
            "ecus": {},
        }

        for idx, tx_addr in enumerate(scan_addrs):
            if _timed_out:
                logger.warning("Scan timed out after %ds", SCAN_TIMEOUT_S)
                break

            if spinner:
                spinner.update_progress(idx + 1, len(scan_addrs))

            ecu_result = scan_ecu_dtcs(panda, tx_addr, obd_bus)
            addr_key = f"0x{tx_addr:03X}"
            scan_result["ecus"][addr_key] = {
                "name": ecu_result["name"],
                "address": ecu_result["address"],
                "dtcs": ecu_result["dtcs"],
                "error": ecu_result["error"],
            }

            # Update cache entry
            cache_entry = {"name": ecu_result["name"], "supports_dtc": ecu_result["supports_dtc"]}
            if not ecu_result["supports_dtc"] and ecu_result["error"]:
                cache_entry["nrc"] = ecu_result["error"]
            cache_data["ecus"][addr_key] = cache_entry

        # Count total DTCs
        scan_result["total_dtcs"] = sum(
            len(ecu["dtcs"]) for ecu in scan_result["ecus"].values()
        )

        # Save results
        _save_results(scan_result)

        # Save ECU cache (only on first discovery, don't overwrite with subset)
        if not ecu_cache:
            params.put("DtcEcuMap", json.dumps(cache_data))
            logger.info("Saved ECU cache with %d ECUs", len(cache_data["ecus"]))

        logger.info(
            "Scan complete: %d ECUs, %d DTCs",
            scan_result["ecus_discovered"], scan_result["total_dtcs"]
        )

    finally:
        if panda is not None:
            try:
                panda.close()
            except Exception:
                pass
        if spinner is not None:
            spinner.close()


def _save_results(scan_result: dict) -> None:
    """Write scan results to temp and persistent locations."""
    try:
        with open(TMP_RESULT_PATH, "w") as f:
            json.dump(scan_result, f, indent=2)
        logger.info("Results written to %s", TMP_RESULT_PATH)
    except OSError:
        logger.exception("Failed to write results to %s", TMP_RESULT_PATH)

    try:
        with open(DTC_RESULT_PATH, "w") as f:
            json.dump(scan_result, f, indent=2)
        logger.info("Results written to %s", DTC_RESULT_PATH)
    except OSError:
        logger.exception("Failed to write results to %s", DTC_RESULT_PATH)


def move_results_to_route(params: Params) -> bool:
    """Copy DTC scan results from /tmp to a locked segment of the current route.

    Called from manager_thread() each iteration after onroad transition.
    Returns True once results are successfully copied, False if not ready
    yet (CurrentRoute not set or no locked segment exists).

    Targets a segment that still has a .lock file, which guarantees the
    uploader has not yet processed it. Prefers segment 0 if still locked,
    otherwise uses whichever segment loggerd is currently writing.
    """
    if not os.path.exists(TMP_RESULT_PATH):
        return True  # Nothing to copy, don't keep retrying

    current_route = params.get("CurrentRoute")
    if not current_route:
        return False

    route_str = current_route.decode("utf-8").strip()
    if not route_str:
        return False

    log_root = Paths.log_root()
    prefix = f"{route_str}--"

    # Find a segment dir that still has a .lock file (safe from uploader)
    target_dir = None
    try:
        for entry in sorted(Path(log_root).iterdir()):
            if not entry.is_dir() or not entry.name.startswith(prefix):
                continue
            if any(f.suffix == ".lock" for f in entry.iterdir()):
                target_dir = entry
                break  # Prefer lowest segment number
    except OSError:
        return False

    if target_dir is None:
        return False  # No locked segments yet, keep polling

    dest = target_dir / "dtc_scan.json"
    try:
        shutil.copy2(TMP_RESULT_PATH, dest)
        logger.info("DTC results copied to %s", dest)
        return True
    except OSError:
        logger.exception("Failed to copy DTC results to %s", dest)
        return True  # Don't retry on write errors
