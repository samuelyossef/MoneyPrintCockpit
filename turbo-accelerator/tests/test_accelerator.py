import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "moneyprinter_accelerator.py"
SPEC = importlib.util.spec_from_file_location("moneyprinter_accelerator", MODULE_PATH)
accelerator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = accelerator
SPEC.loader.exec_module(accelerator)


class _Logger:
    def info(self, *_args):
        pass

    def warning(self, *_args):
        pass


class _Response:
    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield b"video-bytes"


class _Requests:
    def __init__(self):
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return _Response()


class _Config:
    proxy = {}


class _MaterialModule:
    def __init__(self):
        self.requests = _Requests()
        self.config = _Config()

    @staticmethod
    def _get_tls_verify():
        return True


class _Item:
    provider = "pexels"
    url = "https://cdn.example.test/video.mp4?temporary=secret"
    source_info = {"asset_id": "asset-1", "rendition": {"id": "hd"}}


class AcceleratorTests(unittest.TestCase):
    def test_final_download_batch_is_limited_to_remaining_clips(self):
        self.assertEqual(
            accelerator._download_batch_size(audio_duration=12, total_duration=10, max_clip_duration=5, concurrency=3),
            1,
        )
        self.assertEqual(
            accelerator._download_batch_size(audio_duration=30, total_duration=10, max_clip_duration=5, concurrency=3),
            3,
        )

    def test_primary_provider_is_first_and_order_is_deduplicated(self):
        original = os.environ.get("MATERIAL_PROVIDER_ORDER")
        self.addCleanup(
            lambda: os.environ.__setitem__("MATERIAL_PROVIDER_ORDER", original)
            if original is not None
            else os.environ.pop("MATERIAL_PROVIDER_ORDER", None)
        )
        os.environ["MATERIAL_PROVIDER_ORDER"] = "pexels,pixabay,coverr,pexels"
        self.assertEqual(
            accelerator._provider_order("pixabay"),
            ["pixabay", "pexels", "coverr"],
        )

    def test_dns_circuit_opens_after_three_failures_and_resets_after_success(self):
        circuit = accelerator._ProviderCircuit()
        error = RuntimeError("Temporary failure in name resolution")
        self.assertFalse(circuit.failure("pexels", error))
        self.assertFalse(circuit.failure("pexels", error))
        self.assertTrue(circuit.failure("pexels", error))
        self.assertFalse(circuit.available("pexels"))
        circuit.success("pexels")
        self.assertTrue(circuit.available("pexels"))

    def test_cache_lru_cleanup_honors_size_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oldest = root / "oldest.mp4"
            newest = root / "newest.mp4"
            oldest.write_bytes(b"a" * 8)
            newest.write_bytes(b"b" * 8)
            now = time.time()
            os.utime(oldest, (now - 20, now - 20))
            os.utime(newest, (now - 10, now - 10))
            cache = accelerator._BinaryMaterialCache(
                root, max_bytes=10, ttl_seconds=3600, logger=_Logger()
            )
            cache.cleanup()
            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())

    def test_public_url_drops_signatures(self):
        self.assertEqual(
            accelerator._public_url("https://example.test/video.mp4?token=secret"),
            "https://example.test/video.mp4",
        )

    def test_binary_download_is_atomic_and_second_request_is_a_cache_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = accelerator._BinaryMaterialCache(
                root / "cache", max_bytes=1024, ttl_seconds=3600, logger=_Logger()
            )
            module = _MaterialModule()
            original_probe = accelerator._ffprobe_valid
            accelerator._ffprobe_valid = lambda path: Path(path).is_file()
            try:
                first = accelerator._download_to_cache(
                    _Item(), cache, root / "staging", module,
                    accelerator._ProviderCircuit(), _Logger(),
                )
                second = accelerator._download_to_cache(
                    _Item(), cache, root / "staging-second", module,
                    accelerator._ProviderCircuit(), _Logger(),
                )
            finally:
                accelerator._ffprobe_valid = original_probe
            self.assertEqual(module.requests.calls, 1)
            self.assertTrue(Path(first).is_file())
            self.assertTrue(Path(second).is_file())
            self.assertFalse(any((root / "cache").glob("*.part")))


if __name__ == "__main__":
    unittest.main()
