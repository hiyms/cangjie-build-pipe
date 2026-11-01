from __future__ import annotations

import os
from pathlib import Path

import pytest

from cangjie_build.config import BuildConfig, build_config
from cangjie_build.stages._common import base_env, runtime_lib_subdirs


def _tree(tmp_path: Path, target: str, subdirs: list[str]) -> tuple[BuildConfig, Path]:
    """Lay out a compiler output tree with the given runtime lib dirs."""
    cfg = build_config(workspace=tmp_path / "ws", build_root=tmp_path / "bt", target_key=target)
    home = cfg.repo_path("compiler") / "output"
    for subdir in subdirs:
        (home / "runtime" / "lib" / subdir).mkdir(parents=True)
    (home / "bin").mkdir(parents=True, exist_ok=True)
    return cfg, home


def test_runtime_lib_dir_name_has_no_build_type() -> None:
    """The lib directory is named after the platform only.

    cangjie_compiler's CMakeLists derives TARGET_TRIPLE_DIRECTORY_PREFIX from
    CMAKE_SYSTEM_NAME + processor, and cangjie_runtime's config.cmake sets
    TARGET_ARCH the same way; neither puts the build type in this name. The
    build type only names the enclosing output/common/<...> directory.
    """
    for target, expected in (
        ("linux-x64", "linux_x86_64_cjnative"),
        ("windows-x64", "windows_x86_64_cjnative"),
    ):
        assert build_config(target_key=target).target.runtime_lib_subdir() == expected


def test_linux_ld_library_path_contains_the_runtime_lib_dir(tmp_path: Path) -> None:
    cfg, home = _tree(tmp_path, "linux-x64", ["linux_x86_64_cjnative"])
    runtime_lib = home / "runtime" / "lib" / "linux_x86_64_cjnative"

    env = base_env(cfg)

    assert str(runtime_lib) in env["LD_LIBRARY_PATH"].split(os.pathsep)


def test_cross_target_sees_both_host_and_target_runtime_libs(tmp_path: Path) -> None:
    """A cross-compile tree holds the host runtime cjc links against plus the
    target runtime the cross build produces, so both belong on the path."""
    cfg, home = _tree(tmp_path, "windows-x64", ["windows_x86_64_cjnative", "linux_x86_64_cjnative"])

    env = base_env(cfg)
    entries = env["LD_LIBRARY_PATH"].split(os.pathsep)

    assert str(home / "runtime" / "lib" / "windows_x86_64_cjnative") in entries
    assert str(home / "runtime" / "lib" / "linux_x86_64_cjnative") in entries


def test_cross_target_skips_absent_host_runtime(tmp_path: Path) -> None:
    cfg, home = _tree(tmp_path, "windows-x64", ["windows_x86_64_cjnative"])

    entries = base_env(cfg)["LD_LIBRARY_PATH"].split(os.pathsep)

    assert runtime_lib_subdirs(cfg) == ["windows_x86_64_cjnative", "linux_x86_64_cjnative"]
    assert str(home / "runtime" / "lib" / "linux_x86_64_cjnative") not in entries


@pytest.mark.parametrize("build_type", ["release", "debug", "relwithdebinfo"])
def test_build_type_does_not_change_the_runtime_lib_dir(tmp_path: Path, build_type: str) -> None:
    cfg, home = _tree(tmp_path, "linux-x64", ["linux_x86_64_cjnative"])
    cfg = build_config(workspace=cfg.workspace, build_root=cfg.build_root, build_type=build_type)

    env = base_env(cfg)

    assert str(home / "runtime" / "lib" / "linux_x86_64_cjnative") in env["LD_LIBRARY_PATH"].split(
        os.pathsep
    )
