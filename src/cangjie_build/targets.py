from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from cangjie_build.errors import ConfigError

ArchiveFormat = Literal["tar.gz", "zip"]


@dataclass(frozen=True)
class TargetSpec:
    """Static description of a build target."""

    key: str
    sdk_name: str
    archive_format: ArchiveFormat
    exe_suffix: str
    output_dir_suffix: str  # e.g. "x86_64" used in linux_<bt>_<suffix>
    cross_compile: bool
    needs_mingw: bool


class Target(ABC):
    """Strategy describing platform-specific build glue."""

    spec: TargetSpec

    @abstractmethod
    def runtime_output_subdir(self, build_type: str) -> str:
        """Subdir under cangjie_runtime/runtime/output/common/ produced by build.py install."""

    @abstractmethod
    def runtime_lib_subdir(self) -> str:
        """Name of the directory holding this target's runtime libs.

        Under ``compiler/output/runtime/lib/`` in the build tree and under
        ``<sdk>/runtime/lib/`` in the packaged SDK; both use this name. The
        build type selects the enclosing directory only
        (``runtime_output_subdir`` supplies that one), because the CMake side
        derives the lib directory from ``CMAKE_SYSTEM_NAME`` and the processor
        alone: ``TARGET_TRIPLE_DIRECTORY_PREFIX`` in cangjie_compiler's top
        CMakeLists and ``TARGET_ARCH`` in cangjie_runtime's config.cmake.
        """

    @abstractmethod
    def stdx_target_subdir(self) -> str:
        """Subdir under cangjie_stdx/target produced by stdx build.py install."""

    @abstractmethod
    def primary_compiler_output(self) -> str:
        """The compiler output dir to be packaged into software/cangjie."""


class _LinuxX64(Target):
    spec = TargetSpec(
        key="linux-x64",
        sdk_name="linux-x64",
        archive_format="tar.gz",
        exe_suffix="",
        output_dir_suffix="x86_64",
        cross_compile=False,
        needs_mingw=False,
    )

    def runtime_output_subdir(self, build_type: str) -> str:
        return f"linux_{build_type.lower()}_x86_64"

    def runtime_lib_subdir(self) -> str:
        return "linux_x86_64_cjnative"

    def stdx_target_subdir(self) -> str:
        return "linux_x86_64_cjnative"

    def primary_compiler_output(self) -> str:
        return "output"


class _WindowsX64(Target):
    spec = TargetSpec(
        key="windows-x64",
        sdk_name="windows-x64",
        archive_format="zip",
        exe_suffix=".exe",
        output_dir_suffix="x86_64",
        cross_compile=True,
        needs_mingw=True,
    )

    def runtime_output_subdir(self, build_type: str) -> str:
        return f"windows_{build_type.lower()}_x86_64"

    def runtime_lib_subdir(self) -> str:
        return "windows_x86_64_cjnative"

    def stdx_target_subdir(self) -> str:
        return "windows_x86_64_cjnative"

    def primary_compiler_output(self) -> str:
        return "output-x86_64-w64-mingw32"


# The cross-compile builds run their host-side compiler from a linux build
# tree, whose runtime libs therefore live under this name regardless of the
# selected target.
HOST_RUNTIME_LIB_SUBDIR = "linux_x86_64_cjnative"

_REGISTRY: dict[str, Target] = {
    _LinuxX64.spec.key: _LinuxX64(),
    _WindowsX64.spec.key: _WindowsX64(),
}


def get_target(key: str) -> Target:
    try:
        return _REGISTRY[key]
    except KeyError as exc:
        valid = ", ".join(sorted(_REGISTRY))
        raise ConfigError(f"unknown target {key!r}; valid: {valid}") from exc


def all_targets() -> list[str]:
    return sorted(_REGISTRY)
