def toolchain_cache_key(target: str) -> str:
    """Stable identifier for a built toolchain, derived from its pinned inputs.

    The blob holding a built toolchain is keyed by this string, so changing any
    pinned version or commit yields a different key and the old object stops
    being reachable. Deriving it here rather than maintaining it by hand means a
    version bump cannot silently reuse a blob built from different sources.

    The lifetime rule (the storage account deletes objects under the toolchain
    container after 60 days) bounds how long an unreachable object lingers.
    """
    from cangjie_build.errors import ConfigError

    if target == "windows-x64":
        from cangjie_build.toolchain import mingw

        return (
            f"mingw-{mingw.LLVM_MINGW_TAG}"
            f"-llvm{mingw.LLVM_PROJECT_COMMIT[:12]}"
            f"-mingw{mingw.MINGW_W64_COMMIT[:12]}"
            f"-openssl{mingw.OPENSSL_VERSION}"
            f"-{mingw.TARGET_TRIPLE}"
            f"-r{mingw.RECIPE_VERSION}"
        )
    if target == "linux-x64":
        from cangjie_build.toolchain import static_libs

        return (
            f"staticlibs-ncurses{static_libs.NCURSES_VERSION}"
            f"-{static_libs.LIBEDIT_TARBALL}"
            f"-x86_64-linux-gnu"
            f"-r{static_libs.RECIPE_VERSION}"
        )
    raise ConfigError(f"no toolchain is built for target {target!r}")
