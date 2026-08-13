import threading
import time
import unittest

from qwen_worker.server import QwenWorkerRuntime, QwenWorkerServer


class FakeDetector:
    initializations = 0

    def __init__(self):
        type(self).initializations += 1
        self.model_reference = "fake-qwen"
        self.generation_count = 0

    def generate_text(self, _images, prompt, **_options):
        self.generation_count += 1
        if prompt == "slow":
            time.sleep(0.03)
        return "READY" if prompt == "Reply only READY" else prompt


class QwenWorkerTests(unittest.TestCase):
    def setUp(self):
        FakeDetector.initializations = 0

    def test_loopback_only(self):
        runtime = QwenWorkerRuntime(FakeDetector)
        with self.assertRaises(ValueError):
            QwenWorkerServer(runtime, "0.0.0.0", 8792)
        self.assertEqual(QwenWorkerServer(runtime).host, "127.0.0.1")

    def test_ready_only_after_load_and_warmup_and_model_loads_once(self):
        runtime = QwenWorkerRuntime(FakeDetector)
        self.assertEqual(runtime.health()["status"], "STARTING")
        runtime.load()
        health = runtime.health()
        self.assertEqual(health["status"], "READY")
        self.assertTrue(health["model_loaded"])
        self.assertTrue(health["warmed_up"])
        self.assertEqual(health["model_load_count"], 1)
        self.assertEqual(FakeDetector.initializations, 1)
        runtime.generate({"task": "selector", "prompt": "select", "images": []})
        runtime.generate({"task": "semantic", "prompt": "clean", "images": []})
        title = runtime.generate({"task": "title_rewrite", "prompt": "title", "images": []})
        self.assertEqual(runtime.health()["model_load_count"], 1)
        self.assertEqual(runtime.health()["request_count"], 3)
        self.assertEqual(title["task"], "title_rewrite")
        self.assertEqual(FakeDetector.initializations, 1)

    def test_requests_queue_sequentially(self):
        runtime = QwenWorkerRuntime(FakeDetector)
        runtime.load()
        results = []
        threads = [threading.Thread(target=lambda: results.append(runtime.generate({
            "task": "selector", "prompt": "slow", "images": [],
        }))) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 2)
        self.assertTrue(any(item["queue_wait_seconds"] >= 0.02 for item in results))
        self.assertEqual(runtime.health()["status"], "READY")

    def test_failed_warmup_never_reports_ready(self):
        class Broken:
            def __init__(self):
                raise RuntimeError("OOM")
        runtime = QwenWorkerRuntime(Broken)
        runtime.load()
        self.assertEqual(runtime.health()["status"], "ERROR")
        self.assertFalse(runtime.health()["warmed_up"])

    def test_generation_error_restores_ready_state_for_server_recovery_policy(self):
        class BrokenGeneration(FakeDetector):
            def generate_text(self, images, prompt, **options):
                if prompt != "Reply only READY":
                    raise RuntimeError("CUDA out of memory")
                return super().generate_text(images, prompt, **options)
        runtime = QwenWorkerRuntime(BrokenGeneration)
        runtime.load()
        with self.assertRaisesRegex(RuntimeError, "CUDA out of memory"):
            runtime.generate({"task": "semantic", "prompt": "boom", "images": []})
        self.assertEqual(runtime.health()["status"], "READY")


if __name__ == "__main__":
    unittest.main()
