import tempfile
import unittest
import wave
from pathlib import Path

from silence_cutter.vad import _read_pcm_wav


class VadAudioTests(unittest.TestCase):
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
