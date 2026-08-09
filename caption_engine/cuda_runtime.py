from __future__ import annotations

import importlib.util
import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

_CUBLAS_DLL = "cublas64_12.dll"
_CUDNN_DLL = "cudnn64_9.dll"
_DLL_DIRECTORY_HANDLES: dict[Path, object] = {}


@dataclass(frozen=True, slots=True)
class CudaRuntimeStatus:
    applicable: bool
    available: bool
    cublas_dir: Path | None = None
    cudnn_dir: Path | None = None
    cublas_dll_found: bool = False
    cudnn_dll_found: bool = False
    runtime_source: str = "not_applicable"

    def to_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "applicable": self.applicable,
            "available": self.available,
            "cublas_dll": _CUBLAS_DLL,
            "cudnn_dll": _CUDNN_DLL,
            "cublas_found": self.cublas_dll_found,
            "cudnn_found": self.cudnn_dll_found,
            "runtime_source": self.runtime_source,
        }
        if include_paths:
            result["cublas_dir"] = str(self.cublas_dir) if self.cublas_dir else None
            result["cudnn_dir"] = str(self.cudnn_dir) if self.cudnn_dir else None
        return result


def _package_candidates(package: str, component: str) -> list[Path]:
    candidates: list[Path] = []
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ModuleNotFoundError, ValueError):
        spec = None
    if spec is not None:
        for location in spec.submodule_search_locations or ():
            candidates.append(Path(location) / "bin")
        if spec.origin and spec.origin not in ("built-in", "frozen"):
            candidates.append(Path(spec.origin).parent / "bin")

    purelib = sysconfig.get_paths().get("purelib")
    if purelib:
        candidates.append(Path(purelib) / "nvidia" / component / "bin")
    candidates.append(
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / component / "bin"
    )
    return list(dict.fromkeys(candidates))


def _find_runtime_directory(
    package: str, component: str, dll_name: str
) -> Path | None:
    candidates = _package_candidates(package, component)
    for directory in candidates:
        if (directory / dll_name).is_file():
            return directory
    return next((directory for directory in candidates if directory.is_dir()), None)


def _register_directory(directory: Path) -> None:
    resolved = directory.resolve()
    if resolved not in _DLL_DIRECTORY_HANDLES:
        _DLL_DIRECTORY_HANDLES[resolved] = os.add_dll_directory(str(resolved))


def _prepend_process_path(directories: list[Path]) -> None:
    current = os.environ.get("PATH", "")
    entries = [entry for entry in current.split(os.pathsep) if entry]
    known = {os.path.normcase(entry) for entry in entries}
    additions = [str(path.resolve()) for path in directories]
    additions = [path for path in additions if os.path.normcase(path) not in known]
    if additions:
        os.environ["PATH"] = os.pathsep.join(additions + entries)


def prepare_windows_cuda_runtime() -> CudaRuntimeStatus:
    if sys.platform != "win32":
        return CudaRuntimeStatus(applicable=False, available=True)

    cublas_dir = _find_runtime_directory("nvidia.cublas", "cublas", _CUBLAS_DLL)
    cudnn_dir = _find_runtime_directory("nvidia.cudnn", "cudnn", _CUDNN_DLL)
    cublas_found = bool(cublas_dir and (cublas_dir / _CUBLAS_DLL).is_file())
    cudnn_found = bool(cudnn_dir and (cudnn_dir / _CUDNN_DLL).is_file())
    directories = list(dict.fromkeys(
        directory for directory in (cublas_dir, cudnn_dir) if directory is not None
    ))
    for directory in directories:
        _register_directory(directory)
    _prepend_process_path(directories)
    return CudaRuntimeStatus(
        applicable=True,
        available=cublas_found and cudnn_found,
        cublas_dir=cublas_dir,
        cudnn_dir=cudnn_dir,
        cublas_dll_found=cublas_found,
        cudnn_dll_found=cudnn_found,
        runtime_source="python_environment" if directories else "not_found",
    )


def cuda_runtime_error() -> RuntimeError:
    return RuntimeError(
        "CUDA runtime libraries required by faster-whisper were not found.\n\n"
        "Expected:\n"
        "- cublas64_12.dll\n"
        "- cudnn64_9.dll\n\n"
        "Install the project CUDA runtime dependencies:\n"
        "python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
    )
