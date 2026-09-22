"""Safe, local performance extensions for the pinned MoneyPrinterTurbo image.

The upstream image is pinned by digest.  Keeping this module as a tiny overlay
lets us improve its two expensive seams without copying its source tree or any
runtime secrets into this repository.
"""

from __future__ import annotations

import concurrent.futures
import math
import contextvars
import hashlib
import json
import os
import random
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit


_COPY_CONCAT_ALLOWED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "copy_concat_allowed", default=False
)
_STOCK_PROVIDERS = ("pexels", "pixabay", "coverr")


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _download_batch_size(audio_duration: float, total_duration: float, max_clip_duration: float, concurrency: int) -> int:
    """Avoid fetching a full parallel batch when only a final clip is still needed."""
    remaining_duration = max(0.0, audio_duration - total_duration)
    clips_needed = max(1, math.ceil(remaining_duration / max(max_clip_duration, 1.0)))
    return min(concurrency, clips_needed)


def _provider_order(primary: str) -> list[str]:
    configured = [
        item.strip().lower()
        for item in os.getenv("MATERIAL_PROVIDER_ORDER", "pexels,pixabay,coverr").split(
            ","
        )
        if item.strip().lower() in _STOCK_PROVIDERS
    ]
    configured = configured or list(_STOCK_PROVIDERS)
    ordered: list[str] = []
    for provider in [primary, *configured]:
        if provider in _STOCK_PROVIDERS and provider not in ordered:
            ordered.append(provider)
    return ordered


def _safe_filename(value: str) -> str:
    return "".join(char for char in value if char.isalnum() or char in "-_")[:96]


def _public_url(value: str) -> str:
    """Use a stable URL identity without retaining query signatures or tokens."""
    parsed = urlsplit(str(value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _now_ms() -> float:
    return time.perf_counter() * 1000


def _log_metric(logger: Any, stage: str, started_ms: float, **fields: Any) -> None:
    values = [f"stage={stage}", f"duration_ms={_now_ms() - started_ms:.1f}"]
    values.extend(f"{key}={value}" for key, value in fields.items())
    logger.info("material_timing " + " ".join(values))


def _is_dns_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(
        token in text
        for token in (
            "name resolution",
            "getaddrinfo",
            "temporary failure in name resolution",
            "nodename nor servname",
        )
    )


class _ProviderCircuit:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def available(self, provider: str) -> bool:
        with self._lock:
            return time.monotonic() >= self._open_until.get(provider, 0.0)

    def success(self, provider: str) -> None:
        with self._lock:
            self._failures.pop(provider, None)
            self._open_until.pop(provider, None)

    def failure(self, provider: str, error: BaseException) -> bool:
        if not _is_dns_error(error):
            return False
        with self._lock:
            count = self._failures.get(provider, 0) + 1
            self._failures[provider] = count
            if count < 3:
                return False
            self._failures[provider] = 0
            self._open_until[provider] = time.monotonic() + 60.0
            return True


@dataclass
class _CandidateGroup:
    term: str
    providers: list[str]
    search: Callable[[str, str], list[Any]]
    logger: Any
    provider_index: int = 0
    items: list[Any] = field(default_factory=list)
    item_index: int = 0

    def populate_initial(self) -> None:
        self._populate_from(self.provider_index)

    def next_item(self) -> Any | None:
        while self.item_index >= len(self.items):
            if not self._populate_from(self.provider_index + 1):
                return None
        item = self.items[self.item_index]
        self.item_index += 1
        return item

    def _populate_from(self, start_index: int) -> bool:
        for index in range(start_index, len(self.providers)):
            provider = self.providers[index]
            started = _now_ms()
            try:
                items = self.search(provider, self.term)
            except Exception as exc:  # Search failures must not stop a video job.
                self.logger.warning(
                    "stock search failed: "
                    f"provider={provider}, error={type(exc).__name__}"
                )
                items = []
            _log_metric(
                self.logger,
                "material_search",
                started,
                provider=provider,
                result_count=len(items),
            )
            self.provider_index = index
            self.items = list(items)
            self.item_index = 0
            if self.items:
                return True
        return False


class _BinaryMaterialCache:
    """Validated binary cache, separate from task staging and search caches."""

    def __init__(self, root: Path, max_bytes: int, ttl_seconds: int, logger: Any):
        self.root = root
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self.logger = logger
        self.root.mkdir(parents=True, exist_ok=True)
        self._cleanup_lock = threading.Lock()
        # Different jobs can request the same popular stock asset at once.  A
        # fixed set of locks avoids concurrent writes to the same ``.part``
        # file without retaining one lock object forever per asset.
        self._asset_locks = tuple(threading.Lock() for _ in range(128))

    def path_for(self, item: Any) -> Path:
        source = getattr(item, "source_info", {}) or {}
        rendition = source.get("rendition", {}) if isinstance(source, dict) else {}
        payload = {
            "provider": str(getattr(item, "provider", "") or source.get("provider", "")),
            "asset_id": str(source.get("asset_id", "")),
            "rendition": str(rendition.get("id", "")) if isinstance(rendition, dict) else "",
            "url": _public_url(str(getattr(item, "url", ""))),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self.root / f"{digest}.mp4"

    def lock_for(self, path: Path) -> threading.Lock:
        return self._asset_locks[int(path.stem[:8], 16) % len(self._asset_locks)]

    def valid(self, path: Path) -> bool:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if time.time() - path.stat().st_mtime > self.ttl_seconds:
            path.unlink(missing_ok=True)
            return False
        return _ffprobe_valid(path)

    def cleanup(self) -> None:
        with self._cleanup_lock:
            files = []
            total = 0
            now = time.time()
            for path in self.root.glob("*.mp4"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if now - stat.st_mtime > self.ttl_seconds or stat.st_size <= 0:
                    path.unlink(missing_ok=True)
                    continue
                files.append((stat.st_atime, stat.st_mtime, stat.st_size, path))
                total += stat.st_size
            for _, _, size, path in sorted(files):
                if total <= self.max_bytes:
                    break
                path.unlink(missing_ok=True)
                total -= size


def _ffprobe_valid(path: Path) -> bool:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,pix_fmt",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            return False
        streams = json.loads(result.stdout).get("streams", [])
        return bool(streams and int(streams[0].get("width") or 0) > 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _materialize(cache_path: Path, staging_dir: Path) -> str:
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / cache_path.name
    if target.exists():
        return str(target)
    try:
        os.link(cache_path, target)
    except OSError:
        shutil.copy2(cache_path, target)
    return str(target)


def _download_to_cache(
    item: Any,
    cache: _BinaryMaterialCache,
    staging_dir: Path,
    material_module: ModuleType,
    circuit: _ProviderCircuit,
    logger: Any,
) -> str:
    cache_path = cache.path_for(item)
    with cache.lock_for(cache_path):
        return _download_to_cache_locked(
            item, cache, cache_path, staging_dir, material_module, circuit, logger
        )


def _download_to_cache_locked(
    item: Any,
    cache: _BinaryMaterialCache,
    cache_path: Path,
    staging_dir: Path,
    material_module: ModuleType,
    circuit: _ProviderCircuit,
    logger: Any,
) -> str:
    provider = str(getattr(item, "provider", "unknown"))
    started = _now_ms()
    if cache.valid(cache_path):
        os.utime(cache_path, None)
        _log_metric(logger, "material_cache", started, provider=provider, result="hit")
        return _materialize(cache_path, staging_dir)

    if not circuit.available(provider):
        logger.warning(f"stock provider temporarily skipped: provider={provider}")
        return ""

    temp_path = cache_path.with_suffix(".part")
    for attempt in range(3):
        temp_path.unlink(missing_ok=True)
        try:
            response = material_module.requests.get(
                item.url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/115.0.0.0 Safari/537.36"
                    )
                },
                proxies=material_module.config.proxy,
                verify=material_module._get_tls_verify(),
                timeout=(10, 60),
                stream=True,
            )
            response.raise_for_status()
            with temp_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
            if not _ffprobe_valid(temp_path):
                raise ValueError("downloaded material is not a valid video")
            os.replace(temp_path, cache_path)
            circuit.success(provider)
            cache.cleanup()
            _log_metric(
                logger,
                "material_download",
                started,
                provider=provider,
                attempt=attempt + 1,
                result="ok",
            )
            return _materialize(cache_path, staging_dir)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            opened = circuit.failure(provider, exc)
            if opened:
                logger.warning(f"stock provider circuit opened: provider={provider}")
                break
            if attempt < 2:
                delay = (1.0, 3.0)[attempt] + random.uniform(0.0, 0.25)
                time.sleep(delay)
    _log_metric(logger, "material_download", started, provider=provider, result="failed")
    return ""


def _accelerated_stock_download(
    *,
    material_module: ModuleType,
    task_id: str,
    search_terms: list[str],
    source: str,
    video_aspect: Any,
    video_concat_mode: Any,
    audio_duration: float,
    max_clip_duration: int,
    material_directory: str,
    match_script_order: bool,
) -> list[str]:
    logger = material_module.logger
    providers = _provider_order(source)
    searchers = {
        "pexels": material_module.search_videos_pexels,
        "pixabay": material_module.search_videos_pixabay,
        "coverr": material_module.search_videos_coverr,
    }

    def search(provider: str, term: str) -> list[Any]:
        return material_module._search_videos_with_cache(
            provider=provider,
            search_videos=searchers[provider],
            search_term=term,
            minimum_duration=max_clip_duration,
            video_aspect=video_aspect,
        )

    groups = [_CandidateGroup(term, providers, search, logger) for term in search_terms]
    search_workers = _int_env("MATERIAL_SEARCH_CONCURRENCY", 4, 1, 8)
    started = _now_ms()
    with concurrent.futures.ThreadPoolExecutor(max_workers=search_workers) as pool:
        futures = [pool.submit(group.populate_initial) for group in groups]
        for future in futures:
            future.result()
    _log_metric(logger, "material_search_batch", started, terms=len(groups))

    runtime_root = Path(material_module.utils.storage_dir("cache_videos", create=True))
    staging_dir = Path(material_directory) if material_directory else runtime_root / _safe_filename(task_id)
    cache_root = Path(material_module.utils.storage_dir("cache_material_binaries", create=True))
    cache = _BinaryMaterialCache(
        cache_root,
        _int_env("MATERIAL_CACHE_MAX_BYTES", 10 * 1024 * 1024 * 1024, 1, 100 * 1024**3),
        _int_env("MATERIAL_CACHE_TTL_HOURS", 336, 1, 24 * 365) * 3600,
        logger,
    )
    cache.cleanup()
    circuit = _ProviderCircuit()
    download_workers = _int_env("MATERIAL_DOWNLOAD_CONCURRENCY", 3, 1, 4)
    video_paths: list[str] = []
    material_sources: list[dict[str, Any]] = []
    total_duration = 0.0

    concat_mode = getattr(video_concat_mode, "value", video_concat_mode)
    unordered_items: list[Any] = []
    if not match_script_order:
        # Retain the upstream random/sequential choice for legacy requests.  The
        # script-aligned flow below is intentionally round-robin by term.
        unordered_items = [item for group in groups for item in group.items]
        if concat_mode == "random":
            random.shuffle(unordered_items)

    while total_duration <= audio_duration:
        batch_size = _download_batch_size(audio_duration, total_duration, max_clip_duration, download_workers)
        selected: list[tuple[_CandidateGroup | None, Any]] = []
        if match_script_order:
            for group in groups:
                if len(selected) >= batch_size:
                    break
                item = group.next_item()
                if item is not None:
                    selected.append((group, item))
        else:
            for _ in range(batch_size):
                if not unordered_items:
                    break
                selected.append((None, unordered_items.pop(0)))
        if not selected:
            break
        with concurrent.futures.ThreadPoolExecutor(max_workers=download_workers) as pool:
            futures = [
                pool.submit(
                    _download_to_cache,
                    item,
                    cache,
                    staging_dir,
                    material_module,
                    circuit,
                    logger,
                )
                for _, item in selected
            ]
            results = [future.result() for future in futures]
        for (_, item), saved_path in zip(selected, results):
            if not saved_path:
                continue
            if total_duration > audio_duration:
                break
            video_paths.append(saved_path)
            total_duration += min(max_clip_duration, float(item.duration))
            try:
                material_sources.append(material_module._material_source_record(item, saved_path))
            except Exception as exc:
                logger.warning(
                    "failed to prepare material source record: "
                    f"provider={item.provider}, error={type(exc).__name__}"
                )
            if total_duration > audio_duration:
                break

    material_module._persist_material_sources(task_id, material_sources)
    logger.success(
        "downloaded accelerated stock materials: "
        f"count={len(video_paths)}, duration={total_duration:.1f}s"
    )
    return video_paths


def _install_material_patch(material_module: ModuleType) -> None:
    original_download_videos = material_module.download_videos

    def download_videos(
        task_id: str,
        search_terms: list[str],
        source: str = "pexels",
        video_aspect: Any = None,
        video_concat_mode: Any = None,
        audio_duration: float = 0.0,
        max_clip_duration: int = 5,
        match_script_order: bool = False,
    ) -> list[str]:
        if source not in _STOCK_PROVIDERS:
            return original_download_videos(
                task_id,
                search_terms,
                source,
                video_aspect,
                video_concat_mode,
                audio_duration,
                max_clip_duration,
                match_script_order,
            )
        material_directory = str(material_module.config.app.get("material_directory", "")).strip()
        if material_directory == "task":
            material_directory = material_module.utils.task_dir(task_id)
        elif material_directory and not os.path.isdir(material_directory):
            material_directory = ""
        return _accelerated_stock_download(
            material_module=material_module,
            task_id=task_id,
            search_terms=search_terms,
            source=source,
            video_aspect=video_aspect,
            video_concat_mode=video_concat_mode,
            audio_duration=audio_duration,
            max_clip_duration=max_clip_duration,
            material_directory=material_directory,
            match_script_order=match_script_order,
        )

    material_module.download_videos = download_videos


def _probe_signature(path: str) -> tuple[str, int, int, str, str] | None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,pix_fmt,r_frame_rate",
        "-of",
        "json",
        path,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        stream = json.loads(result.stdout).get("streams", [])[0]
        return (
            str(stream["codec_name"]),
            int(stream["width"]),
            int(stream["height"]),
            str(stream["pix_fmt"]),
            str(stream["r_frame_rate"]),
        )
    except (IndexError, KeyError, OSError, ValueError, subprocess.SubprocessError):
        return None


def _install_video_patch(video_module: ModuleType) -> None:
    original_concat = video_module.concat_video_clips_with_ffmpeg
    original_combine = video_module.combine_videos

    def combine_videos(*args: Any, **kwargs: Any) -> Any:
        transition = kwargs.get("video_transition_mode")
        if transition is None and len(args) > 5:
            transition = args[5]
        transition_value = getattr(transition, "value", transition)
        enabled = transition_value in (None, "none")
        token = _COPY_CONCAT_ALLOWED.set(enabled)
        try:
            return original_combine(*args, **kwargs)
        finally:
            _COPY_CONCAT_ALLOWED.reset(token)

    def concat(
        clip_files: list[str],
        output_file: str,
        threads: int,
        output_dir: str,
        max_duration: float | None = None,
    ) -> Any:
        if not _COPY_CONCAT_ALLOWED.get() or not clip_files:
            return original_concat(clip_files, output_file, threads, output_dir, max_duration)
        signatures = [_probe_signature(path) for path in clip_files]
        if not signatures or any(signature is None for signature in signatures):
            return original_concat(clip_files, output_file, threads, output_dir, max_duration)
        if len(set(signatures)) != 1:
            return original_concat(clip_files, output_file, threads, output_dir, max_duration)

        concat_list = os.path.join(output_dir, "ffmpeg-concat-list-copy.txt")
        started = _now_ms()
        try:
            with open(concat_list, "w", encoding="utf-8") as file:
                for clip_file in clip_files:
                    escaped = video_module._format_ffmpeg_concat_path(clip_file)
                    file.write(f"file '{escaped}'\n")
            command = [
                video_module.utils.get_ffmpeg_binary(),
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_list,
                "-map",
                "0:v:0",
                "-c",
                "copy",
            ]
            if max_duration is not None and max_duration > 0:
                command.extend(["-t", f"{max_duration:.3f}"])
            command.append(output_file)
            result = video_module._run_concat_with_heartbeat(command, output_file)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "").strip())
            _log_metric(video_module.logger, "video_concat_copy", started, clips=len(clip_files))
            return "copy"
        except Exception as exc:
            video_module.logger.warning(
                "stream-copy concat unavailable; using safe re-encode fallback: "
                f"error={type(exc).__name__}"
            )
            return original_concat(clip_files, output_file, threads, output_dir, max_duration)
        finally:
            try:
                os.remove(concat_list)
            except OSError:
                pass

    video_module.combine_videos = combine_videos
    video_module.concat_video_clips_with_ffmpeg = concat


def install() -> None:
    """Patch exactly the two upstream seams needed by the local acceleration."""
    try:
        from app.services import material, video
    except Exception:
        # During interpreter tooling that has no Turbo application on sys.path,
        # do nothing.  The normal container command always imports both modules.
        return
    if getattr(material, "_moneyprinter_accelerator_installed", False):
        return
    _install_material_patch(material)
    _install_video_patch(video)
    material._moneyprinter_accelerator_installed = True
    material.logger.info("local material acceleration layer installed")
