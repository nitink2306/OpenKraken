"""Scoped safeguards for the 3014's interleaved HID response stream.

These validate existing liquidctl transactions; they do not invent a Plus
upload protocol. Caller must hold KrakenDevice's I/O lock throughout.
"""

from contextlib import contextmanager
import time


class PlusReplyError(ValueError):
    """An expected reply did not arrive; the cooling connection remains usable."""


def upload_static(driver, path):
    """Use liquidctl's existing RGB565 framebuffer transport (upstream PR #912).

    The 3014 does not implement the bucket protocol. Keep this adapter local
    until liquidctl exposes a stable capability for the corrected path.
    """
    required = ("_prepare_static_file_rgb16", "_send_2023_data_fw2")
    if not all(callable(getattr(driver, name, None)) for name in required):
        raise PlusReplyError("Installed liquidctl lacks the Plus framebuffer transport")
    driver._write([0x30, 0x01])

    def info(msg):
        driver.brightness, driver.orientation = msg[24], msg[26]

    driver._read_until({b"\x31\x01": info})
    data = driver._prepare_static_file_rgb16(path, driver.orientation)
    header = [0x06, 0, 0, 0] + list(len(data).to_bytes(4, "little"))
    # The existing transport sends twice to initialize both framebuffer slots.
    driver._send_2023_data_fw2(data, header)
    driver._send_2023_data_fw2(data, header)


@contextmanager
def checked_replies(driver, *, status=False, budget=1.5):
    from liquidctl.error import Timeout

    deadline = time.monotonic() + budget
    originals = {name: driver.__dict__.get(name) for name in ("_read", "_write_then_read")}
    present = {name: name in driver.__dict__ for name in originals}

    def read_report():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PlusReplyError("Plus HID reply deadline exceeded")
        try:
            report = driver.device.read(64, timeout=max(1, int(remaining * 1000)))
        except Timeout as exc:
            raise PlusReplyError("Plus HID reply timed out") from exc
        if len(report) != 64:
            raise PlusReplyError("Plus returned a short HID report")
        return report

    def matching(prefixes, bucket=None):
        for _ in range(64):
            report = read_report()
            if tuple(report[:2]) in prefixes and (bucket is None or report[14] == bucket):
                return report
        raise PlusReplyError(f"Plus missing reply {prefixes!r}")

    def exchange(data):
        driver._write(data)
        expected = (data[0] + 1, data[1])
        try:
            reply = matching({expected}, data[2] if data[:2] == [0x30, 0x04] else None)
            if expected in {(0x37, 0x01), (0x37, 0x02)} and reply[14] != 1:
                raise PlusReplyError(f"transfer rejected with status {reply[14]:02x}")
            return reply
        except PlusReplyError as exc:
            raise PlusReplyError(
                f"Plus command {bytes(data).hex(' ')}: expected "
                f"{expected[0]:02x} {expected[1]:02x}; {exc}"
            ) from exc

    driver._read = (lambda: matching({(0x75, 0x01), (0x75, 0x02)})) if status else read_report
    driver._write_then_read = exchange
    try:
        yield
    finally:
        for name, original in originals.items():
            if present[name]:
                setattr(driver, name, original)
            else:
                delattr(driver, name)
