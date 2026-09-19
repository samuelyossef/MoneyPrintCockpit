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


# ── Parallel stock-footage search + first-round download ────────────────────────────────────
# _download_videos_by_script_order searches Pexels one term at a time, then downloads one clip at
# a time; measured on a 17-term Short that was 166 s of search (two 30 s read timeouts among
# them) plus 347 s of download, all waiting on the network. The searches don't depend on each
# other, and the loop's first pass always takes each term's first unclaimed candidate, so both
# can run concurrently. The original function still decides everything; it just finds the answers
# already cached.
# ponytail: 4 workers matches the 4 vCPUs Docker sees; Pexels' hourly quota is unchanged (same
# number of searches), raise it if the network, not the CPU, is what's slow.
import importlib.abc
import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor

FETCH_WORKERS = int(os.getenv("TURBO_FETCH_WORKERS", "4"))


def _patch_material(material):
    original, save_video = material._download_videos_by_script_order, material.save_video
    prefetched = {}

    def cached_save_video(video_url, save_dir=""):
        future = prefetched.pop(video_url, None)
        return future.result() if future else save_video(video_url=video_url, save_dir=save_dir)

    def ordered(task_id, search_terms, search_videos, video_aspect, audio_duration, max_clip_duration, material_directory):
        def search(term):
            return search_videos(search_term=term, minimum_duration=max_clip_duration, video_aspect=video_aspect)

        with ThreadPoolExecutor(FETCH_WORKERS) as pool:
            results = dict(zip(search_terms, pool.map(search, search_terms)))
            # Mirrors the original's url de-dup: every url of a term is claimed, its first new one is downloaded first.
            claimed = set()
            for term in search_terms:
                fresh = [item for item in results[term] if item.url not in claimed]
                claimed.update(item.url for item in results[term])
                if fresh:
                    prefetched[fresh[0].url] = pool.submit(save_video, video_url=fresh[0].url, save_dir=material_directory)
            material.save_video = cached_save_video
            try:
                return original(task_id, search_terms, lambda search_term, **_: results[search_term], video_aspect, audio_duration, max_clip_duration, material_directory)
            finally:
                material.save_video = save_video
                for future in prefetched.values():  # downloaded but never needed (the loop stopped early)
                    future.cancel()
                prefetched.clear()

    material._download_videos_by_script_order = ordered


class _MaterialHook(importlib.abc.MetaPathFinder):
    """Patches app.services.material right after Turbo imports it (it doesn't exist yet at startup)."""

    def find_spec(self, name, path, target=None):
        if name != "app.services.material":
            return None
        sys.meta_path.remove(self)
        spec = importlib.util.find_spec(name)
        exec_module = spec.loader.exec_module

        def patched_exec(module):
            exec_module(module)
            try:
                _patch_material(module)
            except Exception:  # a Turbo upgrade that renames these must not break rendering
                pass

        spec.loader.exec_module = patched_exec
        return spec


sys.meta_path.insert(0, _MaterialHook())
