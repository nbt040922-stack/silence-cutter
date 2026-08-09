import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caption_engine.config import CaptionConfig
from caption_engine.cuda_runtime import CudaRuntimeStatus
from caption_engine.transcriber import (
    _clip_timestamps,
    _get_model,
    _load_model,
    transcribe_audio,
)


class CaptionTranscriberTests(unittest.TestCase):
    def setUp(self):
        self.cuda_patcher = patch(
            "caption_engine.cuda_runtime.prepare_windows_cuda_runtime",
            return_value=CudaRuntimeStatus(
                applicable=True,
                available=True,
                cublas_dll_found=True,
                cudnn_dll_found=True,
                runtime_source="python_environment",
            ),
        )
        self.cuda_bootstrap = self.cuda_patcher.start()
        self.addCleanup(self.cuda_patcher.stop)

    def tearDown(self):
        _get_model.cache_clear()

    def test_cuda_failure_is_explicit_without_requested_fallback(self):
        class FakeWhisperModel:
            def __init__(self, _size, *, device, compute_type):
                if device == "cuda":
                    raise OSError("CUDA unavailable")
                self.device = device
                self.compute_type = compute_type

        module = SimpleNamespace(WhisperModel=FakeWhisperModel)
        with patch.dict(sys.modules, {"faster_whisper": module}):
            with self.assertRaisesRegex(RuntimeError, "failed to initialize"):
                _load_model("tiny", "cuda", "float16")
            _get_model.cache_clear()
            model, _, cached = _load_model("tiny", "cpu", "int8")

        self.assertEqual((model.device, model.compute_type), ("cpu", "int8"))
        self.assertFalse(cached)

    def test_full_audio_is_split_into_batch_clips(self):
        self.assertEqual(
            _clip_timestamps(65.0, 30.0),
            [
                {"start": 0.0, "end": 30.0},
                {"start": 30.0, "end": 60.0},
                {"start": 60.0, "end": 65.0},
            ],
        )

    def test_model_cache_hit_is_reported(self):
        class FakeWhisperModel:
            def __init__(self, _size, *, device, compute_type):
                self.device = device
                self.compute_type = compute_type

        with patch.dict(
            sys.modules,
            {"faster_whisper": SimpleNamespace(WhisperModel=FakeWhisperModel)},
        ):
            _load_model("tiny", "cpu", "int8")
            _, _, cached = _load_model("tiny", "cpu", "int8")

        self.assertTrue(cached)

    @patch("caption_engine.transcriber._get_batch_pipeline")
    @patch("caption_engine.transcriber._get_model")
    def test_cuda_inference_can_fallback_when_requested(
        self, get_model, get_batch_pipeline
    ):
        gpu_model = Mock()
        gpu_model.feature_extractor.chunk_length = 30
        gpu_model.model = SimpleNamespace(device="cuda", compute_type="float16")
        cpu_model = Mock()
        cpu_model.feature_extractor.chunk_length = 30
        cpu_model.model = SimpleNamespace(device="cpu", compute_type="int8")
        get_model.side_effect = [gpu_model, cpu_model]
        gpu_pipeline = Mock()
        gpu_pipeline.transcribe.side_effect = RuntimeError("cublas64_12.dll missing")
        cpu_pipeline = Mock()
        info = SimpleNamespace(language="en", language_probability=1.0, duration=1.0)
        cpu_pipeline.transcribe.return_value = (iter([]), info)
        get_batch_pipeline.side_effect = [gpu_pipeline, cpu_pipeline]

        result = transcribe_audio(
            Path("audio.wav"),
            CaptionConfig(allow_cpu_fallback=True),
            audio_duration=1.0,
        )

        self.assertEqual(result.segments, [])
        self.assertEqual(get_model.call_args_list[1].args[1:], ("cpu", "int8"))
        self.assertTrue(result.cpu_fallback_used)
        self.assertEqual(
            (result.actual_device, result.actual_compute_type), ("cpu", "int8")
        )

    @patch("caption_engine.transcriber._get_batch_pipeline")
    @patch("caption_engine.transcriber._get_model")
    def test_batch_output_is_normalized(self, get_model, get_batch_pipeline):
        model = Mock()
        model.feature_extractor.chunk_length = 30
        model.model = SimpleNamespace(device="cuda", compute_type="float16")
        get_model.return_value = model
        raw_word = SimpleNamespace(word="  Officer ", start=1.0, end=1.5, probability=0.9)
        raw_segment = SimpleNamespace(start=1.0, end=1.5, text=" Officer ", words=[raw_word])
        info = SimpleNamespace(language="en", language_probability=0.99, duration=5.0)
        pipeline = get_batch_pipeline.return_value
        pipeline.transcribe.return_value = (iter([raw_segment]), info)

        result = transcribe_audio(
            Path("audio.wav"), CaptionConfig(), audio_duration=65.0
        )

        self.assertEqual(result.segments[0].words[0].text, "Officer")
        self.assertTrue(result.segments[0].words[0].space_before)
        self.assertEqual(result.language, "en")
        self.assertEqual(result.language_probability, 0.99)
        _, kwargs = pipeline.transcribe.call_args
        self.assertEqual(kwargs["batch_size"], 8)
        self.assertTrue(kwargs["word_timestamps"])
        self.assertFalse(kwargs["vad_filter"])
        self.assertEqual(len(kwargs["clip_timestamps"]), 3)
        self.assertTrue(result.manual_clip_timestamps_used)
        self.assertEqual(
            (result.actual_device, result.actual_compute_type), ("cuda", "float16")
        )

    @patch("caption_engine.transcriber._get_batch_pipeline")
    @patch("caption_engine.transcriber._get_model")
    def test_short_batch_uses_native_path_without_manual_clips(
        self, get_model, get_batch_pipeline
    ):
        model = get_model.return_value
        model.feature_extractor.chunk_length = 30
        info = SimpleNamespace(language="en", language_probability=1.0, duration=5.0)
        get_batch_pipeline.return_value.transcribe.return_value = (iter([]), info)

        result = transcribe_audio(Path("audio.wav"), CaptionConfig(), audio_duration=5.0)

        _, kwargs = get_batch_pipeline.return_value.transcribe.call_args
        self.assertNotIn("clip_timestamps", kwargs)
        self.assertFalse(result.manual_clip_timestamps_used)

    @patch("caption_engine.transcriber._get_batch_pipeline")
    @patch("caption_engine.transcriber._get_model")
    def test_normal_mode_uses_whisper_model(self, get_model, get_batch_pipeline):
        model = get_model.return_value
        info = SimpleNamespace(language="vi", language_probability=1.0, duration=0.0)
        model.transcribe.return_value = (iter([]), info)

        result = transcribe_audio(
            Path("audio.wav"), CaptionConfig(batch_enabled=False, language="vi")
        )

        get_batch_pipeline.assert_not_called()
        self.assertEqual(result.segments, [])
        self.assertEqual(result.language, "vi")
        self.assertEqual(result.actual_device, "cuda")
        self.assertIsNone(result.actual_compute_type)

    @patch("caption_engine.transcriber._get_model")
    def test_cpu_mode_does_not_require_cuda_runtime(self, get_model):
        info = SimpleNamespace(language="en", language_probability=1.0, duration=0.0)
        get_model.return_value.transcribe.return_value = (iter([]), info)
        self.cuda_bootstrap.reset_mock()

        result = transcribe_audio(
            Path("audio.wav"),
            CaptionConfig(device="cpu", compute_type="int8", batch_enabled=False),
        )

        self.cuda_bootstrap.assert_not_called()
        self.assertIsNone(result.cuda_runtime)

    def test_cuda_mode_fails_clearly_when_runtime_is_missing(self):
        self.cuda_bootstrap.return_value = CudaRuntimeStatus(
            applicable=True,
            available=False,
            cublas_dll_found=False,
            cudnn_dll_found=False,
            runtime_source="not_found",
        )
        with self.assertRaisesRegex(
            RuntimeError, "python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
        ):
            transcribe_audio(Path("audio.wav"), CaptionConfig(), audio_duration=1.0)

    @patch("caption_engine.transcriber._get_batch_pipeline")
    @patch("caption_engine.transcriber._get_model")
    def test_missing_cuda_runtime_uses_explicit_cpu_fallback(
        self, get_model, get_batch_pipeline
    ):
        self.cuda_bootstrap.return_value = CudaRuntimeStatus(
            applicable=True,
            available=False,
            runtime_source="not_found",
        )
        model = get_model.return_value
        model.feature_extractor.chunk_length = 30
        model.model = SimpleNamespace(device="cpu", compute_type="int8")
        info = SimpleNamespace(language="en", language_probability=1.0, duration=1.0)
        get_batch_pipeline.return_value.transcribe.return_value = (iter([]), info)

        result = transcribe_audio(
            Path("audio.wav"),
            CaptionConfig(allow_cpu_fallback=True),
            audio_duration=1.0,
        )

        self.assertEqual(get_model.call_args.args[1:], ("cpu", "int8"))
        self.assertTrue(result.cpu_fallback_used)
        self.assertFalse(result.cuda_runtime["available"])


if __name__ == "__main__":
    unittest.main()
