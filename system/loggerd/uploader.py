#!/usr/bin/env python3
import bz2
import io
import json
import os
import random
import requests
import threading
import time
import traceback
import datetime
from typing import BinaryIO
from urllib.parse import urlparse
from collections.abc import Iterator

from cereal import log
import cereal.messaging as messaging
from openpilot.common.api import Api
from openpilot.common.params import Params
from openpilot.common.realtime import set_core_affinity
from openpilot.system.hardware.hw import Paths
from openpilot.system.loggerd.xattr_cache import getxattr, setxattr
from openpilot.common.swaglog import cloudlog

from openpilot.frogpilot.common.frogpilot_variables import get_frogpilot_toggles

NetworkType = log.DeviceState.NetworkType
UPLOAD_ATTR_NAME = 'user.upload'
UPLOAD_ATTR_VALUE = b'1'
CURSOR_FILENAME = "_upload_cursor"
SWAGLOG_DIR = "/data/log"
SWAGLOG_CURSOR = "_swaglog_upload_cursor"
SWAGLOG_MTIME_GATE = 60  # seconds since last write before uploading
SEGMENT_MTIME_GATE = 60  # seconds since last write before uploading segment

MAX_UPLOAD_SIZES = {
  "qlog": 25*1e6,  # can't be too restrictive here since we use qlogs to find
                   # bugs, including ones that can cause massive log sizes
  "qcam": 5*1e6,
}

allow_sleep = bool(os.getenv("UPLOADER_SLEEP", "1"))
force_wifi = os.getenv("FORCEWIFI") is not None
fake_upload = os.getenv("FAKEUPLOAD") is not None


class FakeRequest:
  def __init__(self):
    self.headers = {"Content-Length": "0"}


class FakeResponse:
  def __init__(self):
    self.status_code = 200
    self.request = FakeRequest()


def get_directory_sort(d: str) -> list[str]:
  # ensure old format is sorted sooner
  o = ["0", ] if d.startswith("2024-") else ["1", ]
  return o + [s.rjust(10, '0') for s in d.rsplit('--', 1)]

def listdir_by_creation(d: str) -> list[str]:
  if not os.path.isdir(d):
    return []

  try:
    paths = [f for f in os.listdir(d) if os.path.isdir(os.path.join(d, f))]
    paths = sorted(paths, key=get_directory_sort)
    return paths
  except OSError:
    cloudlog.exception("listdir_by_creation failed")
    return []

def clear_locks(root: str) -> None:
  for logdir in os.listdir(root):
    path = os.path.join(root, logdir)
    if not os.path.isdir(path):
      continue
    try:
      for fname in os.listdir(path):
        if fname.endswith(".lock"):
          os.unlink(os.path.join(path, fname))
    except OSError:
      cloudlog.exception("clear_locks failed")


class Uploader:
  def __init__(self, dongle_id: str, root: str):
    self.dongle_id = dongle_id
    self.api = Api(dongle_id)
    self.root = root

    self.params = Params()

    # stats for last successfully uploaded file
    self.last_filename = ""

    self.immediate_folders = ["crash/", "boot/"]
    self.immediate_priority = {"qlog": 0, "qlog.bz2": 0, "qcamera.ts": 1}

    custom_enabled = self.params.get_bool("CustomUploadEnabled")
    if custom_enabled:
      self.custom_server = self.params.get(
        "CustomUploadServer", encoding="utf8"
      )
    else:
      self.custom_server = None
    self.custom_token = self.params.get(
      "CustomUploadToken", encoding="utf8"
    )

    # Upload status tracking
    self._last_upload_time: float = 0.0
    self._last_upload_file: str = ""
    self._current_uploading: str = ""
    self._last_connected: bool = False
    self._has_connected: bool = False
    self._status_update_time: float = 0.0
    self._batch_size: int = 0
    self._upload_state: str = "idle"
    self._prev_total: int = 0
    # Segment upload tracking (custom server, cursor+monotonic)
    self._seg_watch_segment: str = ""
    self._seg_watch_file: str = ""
    self._seg_watch_size: int = 0
    self._seg_watch_time: float = 0.0
    self._seg_current: str = ""
    self._seg_files: list[tuple[str, str, str]] = []
    # Swaglog upload tracking (cursor+monotonic)
    self._swag_watch_file: str = ""
    self._swag_watch_size: int = 0
    self._swag_watch_time: float = 0.0

  def _read_cursor(self) -> str | None:
    """Read the upload cursor (last fully-uploaded segment dir name)."""
    try:
      path = os.path.join(self.root, CURSOR_FILENAME)
      with open(path) as f:
        value = f.read().strip()
      return value if value else None
    except OSError:
      return None

  def _write_cursor(self, dirname: str) -> None:
    """Atomically write the upload cursor."""
    path = os.path.join(self.root, CURSOR_FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
      f.write(dirname)
    os.replace(tmp, path)

  def _has_active_lock(self, path: str, names: list[str]) -> bool:
    """Check if a directory has a lock file."""
    return any(name.endswith(".lock") for name in names)

  def _read_swaglog_cursor(self) -> tuple[str, int] | None:
    """Read swaglog cursor: (filename, size_at_upload)."""
    try:
      path = os.path.join(SWAGLOG_DIR, SWAGLOG_CURSOR)
      with open(path) as f:
        parts = f.read().strip().split()
      if len(parts) == 2:
        return parts[0], int(parts[1])
    except (OSError, ValueError):
      pass
    return None

  def _write_swaglog_cursor(self, filename: str, size: int) -> None:
    """Atomically write swaglog cursor."""
    path = os.path.join(SWAGLOG_DIR, SWAGLOG_CURSOR)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
      f.write(f"{filename} {size}")
    os.replace(tmp, path)

  def next_swaglog_to_upload(self) -> tuple[str, str, str] | None:
    """Return next swaglog file to upload, or None.

    Cursor stores (filename, size) to detect growth after upload.
    A swaglog is only considered complete when its size matches the
    cursor AND a newer file exists (rotation). Uses monotonic time
    for stability gating (immune to GPS clock jumps).
    """
    try:
      entries = sorted(
        f for f in os.listdir(SWAGLOG_DIR) if f.startswith("swaglog.")
      )
    except OSError:
      return None
    if not entries:
      return None

    cursor = self._read_swaglog_cursor()

    if cursor:
      cursor_name, cursor_size = cursor
      cursor_path = os.path.join(SWAGLOG_DIR, cursor_name)

      # Check if cursor file grew since last upload
      try:
        current_size = os.path.getsize(cursor_path)
      except OSError:
        current_size = cursor_size  # Deleted — treat as unchanged

      if current_size != cursor_size:
        # File grew — watch for stability before re-uploading
        if (self._swag_watch_file == cursor_name
                and self._swag_watch_size == current_size
                and time.monotonic() - self._swag_watch_time
                > SWAGLOG_MTIME_GATE):
          key = f"swaglog/{cursor_name}"
          return cursor_name, key, cursor_path
        # Record/update observation (only reset timer on change)
        if (self._swag_watch_file != cursor_name
                or self._swag_watch_size != current_size):
          self._swag_watch_file = cursor_name
          self._swag_watch_size = current_size
          self._swag_watch_time = time.monotonic()
        return None

      # Cursor file unchanged — look for files after it
      try:
        start_idx = entries.index(cursor_name) + 1
      except ValueError:
        start_idx = 0
    else:
      start_idx = 0

    # Upload next file with monotonic stability check
    for name in entries[start_idx:]:
      fn = os.path.join(SWAGLOG_DIR, name)
      try:
        sz = os.path.getsize(fn)
      except OSError:
        continue

      if (self._swag_watch_file == name
              and self._swag_watch_size == sz
              and time.monotonic() - self._swag_watch_time
              > SWAGLOG_MTIME_GATE):
        key = f"swaglog/{name}"
        return name, key, fn

      # Record observation, stop (process sequentially)
      if (self._swag_watch_file != name
              or self._swag_watch_size != sz):
        self._swag_watch_file = name
        self._swag_watch_size = sz
        self._swag_watch_time = time.monotonic()
      return None

    return None

  def _observe_segment_stability(self, logdir: str) -> None:
    """Look-ahead: observe newest file in a segment for stability tracking.

    Updates _seg_watch_* variables so the stability timer starts running
    before we actually need to gate on this segment. Only resets the
    timer when the newest file's name or size changes.
    """
    path = os.path.join(self.root, logdir)
    try:
      names = os.listdir(path)
    except OSError:
      return

    newest_name = ""
    newest_mtime = 0.0
    newest_size = 0
    for n in names:
      if n.endswith(".lock"):
        continue
      fn = os.path.join(path, n)
      try:
        st = os.stat(fn)
        if not st.st_size:
          continue
        if st.st_mtime > newest_mtime:
          newest_mtime = st.st_mtime
          newest_name = n
          newest_size = st.st_size
      except OSError:
        continue

    if not newest_name:
      return

    if (self._seg_watch_segment != logdir
            or self._seg_watch_file != newest_name
            or self._seg_watch_size != newest_size):
      self._seg_watch_segment = logdir
      self._seg_watch_file = newest_name
      self._seg_watch_size = newest_size
      self._seg_watch_time = time.monotonic()

  def _next_custom_segment_file(self) -> tuple[str, str, str] | None:
    """Return next segment file to upload for custom server.

    Uses cursor + monotonic stability check instead of xattr/locks.
    Non-latest segments upload immediately (loggerd has moved on).
    Only the latest segment uses the two-pass stability gate. While
    uploading earlier segments, look-ahead observes the latest segment
    so the stability timer runs concurrently.
    """
    # If we have files queued from the current segment, return next
    if self._seg_files:
      if os.path.isdir(os.path.join(self.root, self._seg_current)):
        return self._seg_files[0]
      # Segment was deleted (e.g. by deleter) — skip it
      cloudlog.event("uploader_segment_deleted", segment=self._seg_current)
      self._seg_files = []
      self._seg_current = ""

    cursor = self._read_cursor()
    cursor_sort = get_directory_sort(cursor) if cursor else None

    # Single pass: find first pending segment and latest dir overall
    first_pending = None
    latest_dir = None
    for logdir in listdir_by_creation(self.root):
      if not logdir[0:1].isdigit():
        continue
      latest_dir = logdir
      if first_pending is None:
        if cursor_sort and get_directory_sort(logdir) <= cursor_sort:
          continue
        first_pending = logdir

    if not first_pending:
      self._seg_watch_segment = ""
      return None

    logdir = first_pending
    path = os.path.join(self.root, logdir)

    try:
      names = [
        n for n in os.listdir(path)
        if not n.endswith(".lock")
        and os.path.isfile(os.path.join(path, n))
      ]
    except OSError:
      return None
    if not names:
      return None

    # Not the latest segment — loggerd has moved on, safe to upload
    if logdir != latest_dir:
      self._seg_current = logdir
      self._seg_files = []
      for n in sorted(names):
        key = os.path.join(logdir, n)
        fn = os.path.join(path, n)
        self._seg_files.append((n, key, fn))
      if self._seg_files:
        # Look-ahead: start stability timer on latest segment
        self._observe_segment_stability(latest_dir)
        return self._seg_files[0]
      return None

    # Latest segment — use stability gate (existing two-pass check)
    newest_name = ""
    newest_mtime = 0.0
    newest_size = 0
    for n in names:
      fn = os.path.join(path, n)
      try:
        st = os.stat(fn)
        if st.st_mtime > newest_mtime:
          newest_mtime = st.st_mtime
          newest_name = n
          newest_size = st.st_size
      except OSError:
        continue

    if not newest_name:
      return None

    # Two-pass stability check (monotonic, immune to clock jumps)
    if (self._seg_watch_segment == logdir
            and self._seg_watch_file == newest_name
            and self._seg_watch_size == newest_size
            and time.monotonic() - self._seg_watch_time
            > SEGMENT_MTIME_GATE):
      # Stable — queue all files for upload
      self._seg_current = logdir
      self._seg_files = []
      for n in sorted(names):
        key = os.path.join(logdir, n)
        fn = os.path.join(path, n)
        self._seg_files.append((n, key, fn))
      if self._seg_files:
        return self._seg_files[0]

    # Record observation (only reset timer when values change)
    if (self._seg_watch_segment != logdir
            or self._seg_watch_file != newest_name
            or self._seg_watch_size != newest_size):
      self._seg_watch_segment = logdir
      self._seg_watch_file = newest_name
      self._seg_watch_size = newest_size
      self._seg_watch_time = time.monotonic()
    return None

  def compute_upload_status(self) -> dict:
    """Compute current upload status for the UI widget.

    Counts segments after cursor. Cursor advances when all files in
    a segment are uploaded, so all segments after cursor are pending.
    """
    cursor = self._read_cursor()
    cursor_sort = get_directory_sort(cursor) if cursor else None

    total_after_cursor = 0

    for logdir in listdir_by_creation(self.root):
      if not logdir[0:1].isdigit():
        continue
      if cursor_sort and get_directory_sort(logdir) <= cursor_sort:
        continue
      total_after_cursor += 1

    # All segments after cursor are pending (cursor advances on completion)
    pending = total_after_cursor

    # Batch tracking for progress bar
    if total_after_cursor == 0:
      pass  # Keep _batch_size for SYNCED display
    elif self._batch_size == 0 or self._prev_total == 0:
      self._batch_size = total_after_cursor  # New batch
    elif total_after_cursor > self._batch_size:
      self._batch_size = total_after_cursor  # Batch grew
    self._prev_total = total_after_cursor

    uploaded_count = max(0, self._batch_size - pending)
    progress = uploaded_count / self._batch_size if self._batch_size > 0 else 1.0

    # Server host
    parsed = urlparse(self.custom_server)
    server_host = parsed.hostname or ""
    if parsed.port:
      server_host += f":{parsed.port}"

    return {
      "uploaded": uploaded_count,
      "total": self._batch_size,
      "progress": progress,
      "state": self._upload_state,
      "connected": self._last_connected,
      "server_host": server_host,
      "has_locked": total_after_cursor > 0,
    }

  def list_upload_files(self, metered: bool) -> Iterator[tuple[str, str, str]]:
    r = self.params.get("AthenadRecentlyViewedRoutes", encoding="utf8")
    requested_routes = [] if r is None else r.split(",")

    cursor = self._read_cursor() if self.custom_server else None
    cursor_sort = get_directory_sort(cursor) if cursor else None

    for logdir in listdir_by_creation(self.root):
      # Skip already-uploaded dirs that sort at or before the cursor
      if cursor_sort and logdir[0:1].isdigit():
        if get_directory_sort(logdir) <= cursor_sort:
          continue

      # Custom server segments handled by _next_custom_segment_file()
      if self.custom_server and logdir[0:1].isdigit():
        continue

      path = os.path.join(self.root, logdir)
      try:
        names = os.listdir(path)
      except OSError:
        continue

      if self._has_active_lock(path, names):
        continue

      for name in sorted(names, key=lambda n: self.immediate_priority.get(n, 1000)):
        if name.endswith(".lock"):
          continue
        key = os.path.join(logdir, name)
        fn = os.path.join(path, name)
        # skip files already uploaded
        try:
          ctime = os.path.getctime(fn)
          is_uploaded = getxattr(fn, UPLOAD_ATTR_NAME) == UPLOAD_ATTR_VALUE
        except OSError:
          cloudlog.event("uploader_getxattr_failed", key=key, fn=fn)
          # deleter could have deleted, so skip
          continue
        if is_uploaded:
          continue

        # limit uploading on metered connections
        if metered:
          dt = datetime.timedelta(hours=12)
          if logdir in self.immediate_folders and (datetime.datetime.now() - datetime.datetime.fromtimestamp(ctime)) < dt:
            continue

          if name == "qcamera.ts" and not any(logdir.startswith(r.split('|')[-1]) for r in requested_routes):
            continue

        yield name, key, fn

  def next_file_to_upload(self, metered: bool) -> tuple[str, str, str] | None:
    upload_files = list(self.list_upload_files(metered))

    # Always prioritize crash/boot logs
    for name, key, fn in upload_files:
      if any(f in fn for f in self.immediate_folders):
        return name, key, fn

    if self.custom_server:
      # Custom server: swaglogs next (same priority tier as boot/crash)
      swaglog = self.next_swaglog_to_upload()
      if swaglog:
        return swaglog

      # Custom server: segments via cursor+mtime (no xattr)
      return self._next_custom_segment_file()

    # Comma/Konik: only upload priority files
    for name, key, fn in upload_files:
      if name in self.immediate_priority:
        return name, key, fn

    return None

  def do_upload(self, key: str, fn: str):
    if self.custom_server:
      url = f"{self.custom_server.rstrip('/')}/{key}"
      headers = {}
      if self.custom_token:
        headers["Authorization"] = f"Bearer {self.custom_token}"

      cloudlog.debug("custom_upload %s -> %s", fn, url)
      with open(fn, "rb") as f:
        return requests.put(url, data=f, headers=headers, timeout=10)

    # Comma/Konik presigned URL flow
    url_resp = self.api.get("v1.4/" + self.dongle_id + "/upload_url/", timeout=10, path=key, access_token=self.api.get_token())
    if url_resp.status_code == 412:
      return url_resp

    url_resp_json = json.loads(url_resp.text)
    url = url_resp_json['url']
    headers = url_resp_json['headers']
    cloudlog.debug("upload_url v1.4 %s %s", url, str(headers))

    if fake_upload:
      return FakeResponse()

    with open(fn, "rb") as f:
      data: BinaryIO
      if key.endswith('.bz2') and not fn.endswith('.bz2'):
        compressed = bz2.compress(f.read())
        data = io.BytesIO(compressed)
      else:
        data = f

      return requests.put(url, data=data, headers=headers, timeout=10)

  def upload(self, name: str, key: str, fn: str, network_type: int, metered: bool) -> bool:
    try:
      sz = os.path.getsize(fn)
    except OSError:
      cloudlog.exception("upload: getsize failed")
      return False

    self._current_uploading = key
    cloudlog.event("upload_start", key=key, fn=fn, sz=sz, network_type=network_type, metered=metered)

    last_exc = None
    if sz == 0:
      # tag files of 0 size as uploaded
      success = True
    elif not self.custom_server and name in MAX_UPLOAD_SIZES and sz > MAX_UPLOAD_SIZES[name]:
      cloudlog.event("uploader_too_large", key=key, fn=fn, sz=sz)
      success = True
    else:
      start_time = time.monotonic()

      stat = None
      last_exc = None
      try:
        stat = self.do_upload(key, fn)
      except Exception as e:
        last_exc = (e, traceback.format_exc())

      if stat is not None and stat.status_code in (200, 201, 401, 403, 412):
        self.last_filename = fn
        dt = time.monotonic() - start_time
        if stat.status_code == 412:
          cloudlog.event("upload_ignored", key=key, fn=fn, sz=sz, network_type=network_type, metered=metered)
        else:
          content_length = int(stat.request.headers.get("Content-Length", 0))
          speed = (content_length / 1e6) / dt
          cloudlog.event("upload_success", key=key, fn=fn, sz=sz, content_length=content_length,
                         network_type=network_type, metered=metered, speed=speed)
        success = True
      else:
        success = False
        self._last_connected = False
        cloudlog.event("upload_failed", stat=stat, exc=last_exc, key=key, fn=fn, sz=sz, network_type=network_type, metered=metered)

    if success:
      self._last_upload_time = time.time()
      self._last_upload_file = key
      self._last_connected = True
      self._has_connected = True

      logdir = key.split("/")[0]
      is_segment = logdir[0:1].isdigit()

      if self.custom_server and logdir == "swaglog":
        # Advance swaglog cursor with current file size
        self._write_swaglog_cursor(name, sz)
      elif self.custom_server and is_segment:
        # Pop from queue, advance cursor when segment is done
        if self._seg_files and self._seg_files[0][1] == key:
          self._seg_files.pop(0)
        if not self._seg_files and self._seg_current:
          cursor = self._read_cursor()
          cursor_sort = get_directory_sort(cursor) if cursor else None
          if not cursor_sort or get_directory_sort(self._seg_current) > cursor_sort:
            self._write_cursor(self._seg_current)
          self._seg_current = ""
      else:
        # Boot/crash or comma path: use xattr
        try:
          setxattr(fn, UPLOAD_ATTR_NAME, UPLOAD_ATTR_VALUE)
        except OSError:
          cloudlog.event("uploader_setxattr_failed", exc=last_exc, key=key, fn=fn, sz=sz)

    self._current_uploading = ""
    return success


  def step(self, network_type: int, metered: bool) -> bool | None:
    d = self.next_file_to_upload(metered)
    if d is None:
      return None

    name, key, fn = d

    # qlogs and bootlogs need to be compressed before uploading
    # (skip compression for custom server -- send raw)
    if not self.custom_server:
      if key.endswith(('qlog', 'rlog')) or (key.startswith('boot/') and not key.endswith('.bz2')):
        key += ".bz2"

    return self.upload(name, key, fn, network_type, metered)


def main(exit_event: threading.Event = None) -> None:
  if exit_event is None:
    exit_event = threading.Event()

  try:
    set_core_affinity([0, 1, 2, 3])
  except Exception:
    cloudlog.exception("failed to set core affinity")

  clear_locks(Paths.log_root())

  params = Params()
  dongle_id = params.get("DongleId", encoding='utf8')

  if dongle_id is None:
    cloudlog.info("uploader missing dongle_id")
    raise Exception("uploader can't start without dongle id")

  sm = messaging.SubMaster(['deviceState', 'frogpilotPlan'])
  uploader = Uploader(dongle_id, Paths.log_root())

  # Clear stale status from previous boot so widget doesn't flash ERROR
  params.remove("UploaderStatus")

  backoff = 0.1

  # FrogPilot variables
  frogpilot_toggles = get_frogpilot_toggles()

  prev_offroad = params.get_bool("IsOffroad")
  prev_network = NetworkType.none

  while not exit_event.is_set():
    sm.update(0)
    offroad = params.get_bool("IsOffroad")
    network_type = sm['deviceState'].networkType if not force_wifi else NetworkType.wifi
    at_home = offroad and network_type in (NetworkType.ethernet, NetworkType.wifi) or not frogpilot_toggles.no_onroad_uploads

    # Reset backoff on state transitions
    if offroad != prev_offroad or network_type != prev_network:
      backoff = 0
      prev_offroad = offroad
      prev_network = network_type

    if network_type == NetworkType.none or not at_home:
      if uploader.custom_server:
        uploader._upload_state = "no_network"
        now = time.monotonic()
        if now - uploader._status_update_time >= 10:
          status = uploader.compute_upload_status()
          uploader.params.put_nonblocking("UploaderStatus", json.dumps(status))
          uploader._status_update_time = now
      if allow_sleep:
        time.sleep(5)
      continue

    success = uploader.step(sm['deviceState'].networkType.raw, sm['deviceState'].networkMetered)

    if uploader.custom_server:
      if success is True:
        uploader._upload_state = "uploading"
        backoff = 0.1
      elif success is None:
        uploader._upload_state = "idle"
        backoff = 5 * 60
      else:  # False — upload HTTP failure
        if uploader._has_connected:
          uploader._upload_state = "error"
        # Before first success, stay "idle" (network may not be ready)
        backoff = 5 * 60

      # Pending segments: poll faster regardless of status update timing
      if uploader._upload_state == "idle" and backoff > 10:
        status = uploader.compute_upload_status()
        if status["has_locked"]:
          uploader._upload_state = "uploading"
          backoff = 10

      # Write upload status for UI widget
      now = time.monotonic()
      if success or (now - uploader._status_update_time >= 10):
        status = uploader.compute_upload_status()
        status["state"] = uploader._upload_state
        uploader.params.put_nonblocking("UploaderStatus", json.dumps(status))
        uploader._status_update_time = now
    else:
      if success is None:
        backoff = 60 if offroad else 5
      elif success:
        backoff = 0.1
      else:
        cloudlog.info("upload backoff %r", backoff)
        backoff = min(backoff*2, 120)
    if allow_sleep:
      time.sleep(backoff + random.uniform(0, backoff))

    # Update FrogPilot variables
    if sm['frogpilotPlan'].togglesUpdated:
      frogpilot_toggles = get_frogpilot_toggles()
      custom_enabled = params.get_bool("CustomUploadEnabled")
      if custom_enabled:
        uploader.custom_server = params.get(
          "CustomUploadServer", encoding="utf8"
        )
      else:
        uploader.custom_server = None
      uploader.custom_token = params.get(
        "CustomUploadToken", encoding="utf8"
      )

if __name__ == "__main__":
  main()
