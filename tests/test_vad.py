import tempfile
import unittest
import wave
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from silence_cutter.vad import _read_pcm_wav, detect_speech


class VadAudioTests(unittest.TestCase):
    def test_silero_internal_padding_is_disabled(self):
        timestamps = Mock(return_value=[])
        module = SimpleNamespace(
            get_speech_timestamps=timestamps,
            read_audio=Mock(return_value=object()),
        )
        with patch.dict("sys.modules", {"silero_vad": module}), patch(
            "silence_cutter.vad._model", return_value=object()
        ):
            detect_speech(Path("analysis.wav"))
        self.assertEqual(timestamps.call_args.kwargs["speech_pad_ms"], 0)

    def test_reads_analysis_pcm_without_torchaudio_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16_000)
                wav.writeframes(b"\x00\x00\xff\x7f\x00\x80")

            audio = _read_pcm_wav(path, 16_000)

        self.assertEqual(audio.shape[0], 3)
        self.assertAlmostEqual(audio[0].item(), 0.0)
        self.assertGreater(audio[1].item(), 0.99)
        self.assertEqual(audio[2].item(), -1.0)


if __name__ == "__main__":
    unittest.main()
