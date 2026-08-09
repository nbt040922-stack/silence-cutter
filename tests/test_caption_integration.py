import os
import unittest
from pathlib import Path

from caption_engine.config import CaptionConfig
from caption_engine.transcriber import transcribe_audio


ENABLED = os.getenv("CAPTION_ENGINE_INTEGRATION") == "1"
MEDIA = os.getenv("CAPTION_ENGINE_TEST_MEDIA")


@unittest.skipUnless(ENABLED and MEDIA, "set caption integration environment flags")
class CaptionModelIntegrationTests(unittest.TestCase):
    def test_real_model_inference(self):
        result = transcribe_audio(
            Path(MEDIA),
            CaptionConfig(
                model_size=os.getenv("CAPTION_ENGINE_TEST_MODEL", "tiny"),
                device=os.getenv("CAPTION_ENGINE_TEST_DEVICE", "cpu"),
                compute_type=os.getenv("CAPTION_ENGINE_TEST_COMPUTE", "int8"),
                batch_enabled=False,
            ),
        )
        self.assertIsInstance(result.segments, list)


if __name__ == "__main__":
    unittest.main()
