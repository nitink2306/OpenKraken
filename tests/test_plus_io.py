"""Replay the Plus report mix without opening hardware."""
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from liquidctl.error import Timeout
from openkraken.backend.device import KrakenDevice
from openkraken.backend.plus_io import PlusReplyError, checked_replies
from openkraken.backend import lcd_render


def report(prefix, values=None):
    result = list(prefix) + [0] * 62
    if prefix in ((0x37, 0x01), (0x37, 0x02)):
        result[14] = 1
    for index, value in (values or {}).items():
        result[index] = value
    return result


class Driver:
    product_id = 0x3014
    lcd_resolution = (240, 240)

    def __init__(self, reports=()):
        self.device = self
        self.reports = iter(reports)
        self.writes = []
        self.calls = 0
        self.timeouts = []

    def read(self, length, *, timeout):
        self.timeouts.append(timeout)
        value = next(self.reports, None)
        if value is None:
            raise Timeout()
        return value

    def _write(self, data):
        self.writes.append(data)

    def _read(self):
        raise RuntimeError('unfiltered read')

    def _write_then_read(self, data):
        raise RuntimeError('unfiltered exchange')

    def set_screen(self, channel, mode, value):
        self.calls += 1
        self._write_then_read([0x30, 0x04, 0])

    def _read_until(self, parsers):
        self.calls += 1
        msg = self._read()
        parser = parsers.get(bytes(msg[:2]))
        if parser is None:
            raise PlusReplyError('missing LCD info')
        parser(msg)

    def _prepare_static_file_rgb16(self, path, orientation):
        return [0] * (240 * 240 * 2)

    def _send_2023_data_fw2(self, data, header):
        self._write_then_read([0x36, 0x01, 0, 1, 6])
        self._write_then_read([0x36, 0x02])

    def get_status(self):
        msg = self._read()
        return [('Liquid temperature', msg[15] + msg[16] / 10, 'C')]


class PlusTest(unittest.TestCase):
    def device(self, driver):
        device = KrakenDevice()
        device._dev = driver
        device._connected = True
        def cleanup():
            if device._bucket_watcher:
                logging.getLogger('liquidctl.driver.kraken3').removeHandler(device._bucket_watcher)
        self.addCleanup(cleanup)
        return device

    def test_bucket_query_skips_telemetry_and_wrong_bucket(self):
        correct = report((0x31, 0x04), {14: 0})
        driver = Driver([report((0x75, 0x02)), report((0x31, 0x04), {14: 1}), correct])
        with checked_replies(driver):
            self.assertEqual(driver._write_then_read([0x30, 0x04, 0]), correct)
        self.assertNotIn('_read', driver.__dict__)
        self.assertNotIn('_write_then_read', driver.__dict__)

    def test_missing_query_reply_aborts_before_bucket_mutation(self):
        driver = Driver([report((0x75, 0x02))])
        device = self.device(driver)
        self.assertFalse(device.set_lcd_static('unused.png'))
        self.assertEqual(driver.writes, [[0x30, 0x01]])
        self.assertEqual(driver.calls, 1)
        self.assertTrue(device.is_connected)
        self.assertTrue(all(0 < t <= 1500 for t in driver.timeouts))
        self.assertNotIn('_read', driver.__dict__)

    def test_status_ignores_ack_that_previously_looked_like_two_degrees(self):
        driver = Driver([report((0xff, 0x01), {15: 2}), report((0x75, 0x01), {15: 28, 16: 8})])
        self.assertEqual(self.device(driver).get_status().liquid_temp, 28.8)

    def test_missing_status_is_unknown_and_keeps_connection(self):
        status = self.device(Driver()).get_status()
        self.assertIsNone(status.liquid_temp)
        self.assertTrue(status.connected)

    def test_failed_sensor_upload_backs_off_without_ring_or_liquid_reset(self):
        driver = Driver()
        device = self.device(driver)
        with patch('openkraken.backend.device.time.monotonic', return_value=10):
            self.assertFalse(device.set_lcd_sensor_frame('unused.png'))
            self.assertFalse(device.set_lcd_sensor_frame('unused.png'))
        self.assertEqual(driver.calls, 1)
        with patch('openkraken.backend.device.time.monotonic', return_value=41):
            self.assertFalse(device.set_lcd_sensor_frame('unused.png'))
        self.assertEqual(driver.calls, 2)

    def test_deadline_is_shared_across_reports(self):
        driver = Driver([report((0x75, 0x02))])
        with patch('openkraken.backend.plus_io.time.monotonic', side_effect=[0, 0.5, 1.6]):
            with self.assertRaises(PlusReplyError), checked_replies(driver):
                driver._write_then_read([0x30, 0x04, 0])
        self.assertEqual(driver.timeouts, [1000])

    def test_short_reply_is_rejected(self):
        driver = Driver([[0x31, 0x04]])
        with self.assertRaises(PlusReplyError), checked_replies(driver):
            driver._write_then_read([0x30, 0x04, 0])

    def test_plus_framebuffer_upload_never_uses_buckets(self):
        driver = Driver([report((0x31, 0x01)), report((0x75, 0x02)),
                         report((0x37, 0x01)), report((0x37, 0x02)),
                         report((0x37, 0x01)), report((0x37, 0x02))])
        self.assertTrue(self.device(driver).set_lcd_static('unused.png'))
        self.assertEqual(driver.writes, [[0x30, 0x01],
                         [0x36, 0x01, 0, 1, 6], [0x36, 0x02],
                         [0x36, 0x01, 0, 1, 6], [0x36, 0x02]])

    def test_sensor_stream_modes_preserve_plus_transport(self):
        for mode in ("auto", "static", "ring"):
            with self.subTest(mode=mode):
                driver = Driver([report((0x31, 0x01)), report((0x75, 0x02)),
                                 report((0x37, 0x01)), report((0x37, 0x02)),
                                 report((0x37, 0x01)), report((0x37, 0x02))])
                device = self.device(driver)
                device.lcd_stream_mode = mode
                self.assertEqual(device.lcd_stream_path(), "static")
                self.assertTrue(device.set_lcd_sensor_frame('unused.png'))
                self.assertEqual(driver.writes, [[0x30, 0x01],
                                 [0x36, 0x01, 0, 1, 6], [0x36, 0x02],
                                 [0x36, 0x01, 0, 1, 6], [0x36, 0x02]])

    def test_rejected_transfer_cannot_report_success(self):
        driver = Driver([report((0x31, 0x01)), report((0x37, 0x01), {14: 0})])
        device = self.device(driver)
        self.assertFalse(device.set_lcd_static('unused.png'))
        self.assertTrue(device.is_connected)
        self.assertEqual(len(driver.writes), 2)

    def test_gif_and_clear_fail_without_usb_traffic(self):
        driver = Driver()
        device = self.device(driver)
        self.assertFalse(device.set_lcd_gif('unused.gif'))
        self.assertFalse(device.clear_lcd_media())
        self.assertEqual(driver.writes, [])
        self.assertTrue(device.is_connected)

    def test_render_resolution(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'frame.png')
            lcd_render.render_to_file('liquid_ring', lcd_render.LcdData(28.8, 40, 10, 35, 5, 2549, 1369), path, (240, 240))
            with Image.open(path) as image:
                self.assertEqual(image.size, (240, 240))


if __name__ == '__main__':
    unittest.main()
