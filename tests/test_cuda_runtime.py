import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import caption_engine.cuda_runtime as cuda_runtime


class WindowsCudaRuntimeTests(unittest.TestCase):
    def setUp(self):
        cuda_runtime._DLL_DIRECTORY_HANDLES.clear()
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.cublas = root / "nvidia" / "cublas" / "bin"
        self.cudnn = root / "nvidia" / "cudnn" / "bin"
        self.cublas.mkdir(parents=True)
        self.cudnn.mkdir(parents=True)

    def tearDown(self):
        cuda_runtime._DLL_DIRECTORY_HANDLES.clear()
        self.directory.cleanup()

    def _prepare(self, *, cublas: bool, cudnn: bool):
        if cublas:
            (self.cublas / "cublas64_12.dll").touch()
        if cudnn:
            (self.cudnn / "cudnn64_9.dll").touch()
        candidates = lambda package, _component: (
            [self.cublas] if package == "nvidia.cublas" else [self.cudnn]
        )
        add_dll_directory = Mock(side_effect=lambda path: object())
        with (
            patch.object(cuda_runtime.sys, "platform", "win32"),
            patch.object(cuda_runtime, "_package_candidates", side_effect=candidates),
            patch.object(cuda_runtime.os, "add_dll_directory", add_dll_directory, create=True),
            patch.dict(os.environ, {"PATH": "existing"}),
        ):
            status = cuda_runtime.prepare_windows_cuda_runtime()
            process_path = os.environ["PATH"]
        return status, process_path, add_dll_directory

    def test_discovers_and_registers_both_runtime_directories(self):
        status, process_path, add_dll_directory = self._prepare(
            cublas=True, cudnn=True
        )
        self.assertTrue(status.available)
        self.assertEqual((status.cublas_dir, status.cudnn_dir), (self.cublas, self.cudnn))
        self.assertEqual(add_dll_directory.call_count, 2)
        self.assertIn(str(self.cublas.resolve()), process_path)
        self.assertIn(str(self.cudnn.resolve()), process_path)

    def test_missing_cublas_is_reported(self):
        status, _, _ = self._prepare(cublas=False, cudnn=True)
        self.assertFalse(status.available)
        self.assertFalse(status.cublas_dll_found)
        self.assertTrue(status.cudnn_dll_found)

    def test_missing_cudnn_is_reported(self):
        status, _, _ = self._prepare(cublas=True, cudnn=False)
        self.assertFalse(status.available)
        self.assertTrue(status.cublas_dll_found)
        self.assertFalse(status.cudnn_dll_found)

    def test_both_missing_are_reported(self):
        status, _, _ = self._prepare(cublas=False, cudnn=False)
        self.assertFalse(status.available)
        self.assertFalse(status.cublas_dll_found)
        self.assertFalse(status.cudnn_dll_found)

    def test_non_windows_is_no_op(self):
        add_dll_directory = Mock()
        original_path = os.environ.get("PATH")
        with (
            patch.object(cuda_runtime.sys, "platform", "linux"),
            patch.object(cuda_runtime.os, "add_dll_directory", add_dll_directory, create=True),
        ):
            status = cuda_runtime.prepare_windows_cuda_runtime()
        self.assertFalse(status.applicable)
        self.assertTrue(status.available)
        self.assertEqual(os.environ.get("PATH"), original_path)
        add_dll_directory.assert_not_called()

    def test_path_change_is_process_local_and_handles_stay_alive(self):
        _, process_path, _ = self._prepare(cublas=True, cudnn=True)
        self.assertTrue(process_path.endswith("existing"))
        self.assertEqual(len(cuda_runtime._DLL_DIRECTORY_HANDLES), 2)
        self.assertTrue(all(cuda_runtime._DLL_DIRECTORY_HANDLES.values()))

    def test_no_project_path_is_hard_coded(self):
        source = inspect.getsource(cuda_runtime)
        self.assertNotIn("D:\\Silence_cutter", source)
        self.assertNotIn(".venv", source)


if __name__ == "__main__":
    unittest.main()
