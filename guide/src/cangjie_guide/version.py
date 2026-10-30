from __future__ import annotations

import re

from cangjie_guide.errors import GuideError

# Mirrors src/cangjie_build/config.py in the harness. Duplicated rather than
# imported because guide/ is a separate project: importing the harness would make
# the two share an interpreter and a lockfile, which is exactly what they must
# not do. tests/test_version.py pins this copy against the harness source.
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
FALLBACK_VERSION = "0.0.0-dev"


def normalise(raw: str) -> str:
    """The version the packaging stage will bake into artifact names.

    A branch name is not SemVer, so it collapses to the fallback: tag=main and
    tag=dev both produce 0.0.0-dev, which is why two builds of different code but
    the same ref name land on the same artifact name.
    """
    candidate = raw.strip()
    if candidate[:1] in ("v", "V"):
        candidate = candidate[1:]
    if SEMVER_PATTERN.match(candidate):
        return candidate
    return FALLBACK_VERSION


def explicit(raw: str) -> str:
    """A version the caller asked for, checked instead of normalised.

    The harness strips a leading v and rewrites anything else into the dev
    placeholder. Doing that to an explicit version would silently build and
    archive something other than what was asked for, so an explicit version has
    to already be the exact SemVer the runtime parses at startup.
    """
    candidate = raw.strip()
    if candidate[:1] in ("v", "V"):
        raise GuideError(
            f"version {raw!r} starts with v; pass it without the prefix ({candidate[1:]})"
        )
    if not SEMVER_PATTERN.match(candidate):
        raise GuideError(f"version {raw!r} is not MAJOR.MINOR.PATCH (optionally +prerelease)")
    return candidate
