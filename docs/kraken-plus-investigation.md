# Kraken 2024 Plus investigation

Device: `1e71:3014`, firmware `1.0.0`, LCD 240×240. Investigation performed
October 13–14, 2026 on the user's connected cooler. OpenKraken 0.4.1;
system liquidctl provides the existing Kraken 2023 firmware-2 framebuffer helpers.

## Findings

The supplied `~/Downloads/debug-logs.log` shows:

- At 23:15:15, status polling consumes `ff 01` (an acknowledgement) as sensor
  data. This explains a bogus 2°C/zero-RPM sample; it is not evidence of faulty sensors.
- LCD-info (`30 01` → `31 01`) and transfer preamble (`36 03` → `37 03`) succeed.
- The first bucket query (`30 04 00`) receives unsolicited `75 02` telemetry.
  Subsequent queries similarly consume telemetry as bucket metadata.
- Bucket deletion (`32 02`) receives `ff 02`, followed by telemetry instead of
  the expected `33 02`. The final assertion is downstream of the first bad reply.
- Repeating that complete upload three times stretches the stall to about 80 seconds.

A guarded **direct call to the installed liquidctl driver's `set_screen`** on
this hardware reproduced the missing `31 04` reply for `30 04 00`; it was stopped
at 1.5 seconds before invalid bucket data could be used. This comparison changes
reply validation/timeouts, not the driver's upload selection. We did not repeat
the unguarded 80-second failure. The existing application was stopped before
exclusive tests and restarted afterwards.

Resolution alone is not the cause: liquidctl already resizes both static and
GIF input using its per-device `lcd_resolution`.

Upstream references:

- [liquidctl issue #908](https://github.com/liquidctl/liquidctl/issues/908)
- [liquidctl proposed fix #912](https://github.com/liquidctl/liquidctl/pull/912)

The proposed upstream fix routes Plus static images through the existing RGB565
framebuffer transport, avoiding buckets. OpenKraken now uses those driver helpers
only for `3014`. Native GIF playback is unsupported on this path and is rejected
without USB traffic; live sensor screens use successive static frames.

## Changes

- Per-device output resolution, with high-quality downsampling of the established
  640×640 layouts to 240×240 for Plus.
- Plus static/frame uploads use the framebuffer path, without liquid-mode resets,
  custom ring allocation, bucket clearing, or nested upload retries.
- Scoped reply filtering under the existing I/O lock; original driver methods are
  restored on success and failure. Status cannot parse command acknowledgements.
- 1.5-second shared HID deadline and 30-second automatic-frame backoff after failure.
  Missing replies preserve the cooling connection and saved configuration.
- Transfer acknowledgements must report success. Diagnostics identify the command
  and expected reply rather than asserting a competing process exists.
- Existing non-Plus upload/ring path is retained.

The HID deadline is **not a hard deadline for all work**: image decoding, USB bulk
writes, driver queue flushing, and OS scheduling remain synchronous. No concurrent
reader was introduced on the shared HID interface. Large/malformed files or a
stalled bulk endpoint need separate scheduling work if hard real-time guarantees
are required.

## Validation

`python -m unittest discover -s tests`: **28 tests passed**. New tests cover
interleaved telemetry, bucket-index validation, short/missing/rejected replies,
shared deadlines, restoration of driver methods, unknown status on timeout,
framebuffer selection, frame backoff, GIF rejection, and output resolution.
Existing bucket-ring regressions pass.

Hardware results are recorded in:

- `kraken-plus-hardware-results.json`
- `kraken-plus-comparison-results.json`

Static transfer: 0.118 seconds initially; 0.086 seconds immediately after the
failed direct-driver comparison. Three frame transfers: 0.103–0.104 seconds.
Status reads between frames: 0.001 seconds, pump duty 75%, approximately 2540–2554 RPM.
Liquid mode, brightness, and orientation commands succeeded. The user visually
confirmed the purple live sensor screen during continuous rendering (the brief
initial test had already restored the default screen before confirmation).

A sustained run recorded 30 successful frames and 73 status polls over about
71 seconds with no warnings/errors. Application restart/reconnection succeeded.
The final app runs the CPU/GPU `triple` sensor layout with normal logging.
Physical cold boot, cable disconnection, long-duration endurance, rotated-image
visual inspection, and Elite hardware testing remain outstanding. GIF rejection
was tested; animated GIF playback was not implemented.

## Cooling and local deployment

The CPU-curve test used pump fixed at 75% and CPU fan points
`(40,25), (60,75), (80,100)`, retaining the existing 48–54°C liquid-temperature
firmware failsafe. After testing, the user chose liquid-temperature control.
The current local settings are pump fixed at 75% and the firmware-native Silent
fan curve: `(20,20), (34,30), (40,45), (46,70), (50,100)`. That fan curve continues
without the userspace engine. These are local preferences, not changed application
defaults, and personal configuration files are not included in this repository.

The original configuration is backed up at
`~/.config/openkraken/config.before-plus-fix.json`.

The source-checkout run was used for hardware validation. Persistent deployment
uses the fork's native Arch package (`0.4.1-2`) and an XDG login-autostart entry
running `/usr/bin/openkraken --minimized`. The package copies the code into
`/usr/lib/openkraken`, independent of the development checkout.

Local runtime logs and configuration backups are not part of this repository.
See [maintaining this fork](maintaining-plus-fork.md) for installation, autostart,
and the reviewed upstream-update workflow.
