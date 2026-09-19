"""Faster x264 settings for MoneyPrinterTurbo, injected via PYTHONPATH (see compose.yaml).

Turbo calls moviepy's write_videofile without a `preset`, so every clip is encoded with
moviepy's default "medium". It re-encodes one temp clip per cut, so that default dominates
the render: on this box (i5-7200U, 2 cores) a single 3 s vertical clip measured

    preset=medium (default)  45.8 s   5.24 MB
    preset=veryfast          16.2 s   4.75 MB   <- same -crf 23, smaller file
    preset=ultrafast          7.3 s  10.83 MB   <- worse compression, skipped

x264 presets trade encoder CPU for compression efficiency at a fixed -crf, not for picture
quality, so veryfast targets the same quality ~2.8x faster. Patching moviepy instead of
Turbo's own source keeps this working across Turbo upgrades.

ponytail: bump the preset back toward "medium" if storage ever costs more than render time.
"""

import os

PRESET = os.getenv("TURBO_X264_PRESET", "veryfast")
# Turbo passes n_threads only on the final merge; the per-clip loop leaves ffmpeg to guess.
THREADS = int(os.getenv("TURBO_ENCODE_THREADS", "0")) or (os.cpu_count() or 2)

try:
    from moviepy.video.VideoClip import VideoClip
except Exception:  # moviepy missing or broken: never take the whole interpreter down
    pass
else:
    _write_videofile = VideoClip.write_videofile

    def write_videofile(self, *args, **kwargs):
        kwargs.setdefault("preset", PRESET)
        kwargs.setdefault("threads", THREADS)
        return _write_videofile(self, *args, **kwargs)

    VideoClip.write_videofile = write_videofile
