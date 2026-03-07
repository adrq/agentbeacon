"""Binary discovery logic following the ruff/uv pattern."""

from __future__ import annotations

import os
import sys
import sysconfig


def _find_binary(name: str) -> str:
    """Return the absolute path to a named binary.

    Search order (matches ruff's actual installed __main__.py):
    1. sysconfig scripts dir (active venv / system)
    2. User scheme scripts dir
    3. bin/ adjacent to package root (pip install --target)
    4. pip build-env overlay detection
    """
    exe_suffix = sysconfig.get_config_var("EXE") or ""
    exe_name = name + exe_suffix

    # 1. Active venv / system scripts directory.
    scripts_path = os.path.join(sysconfig.get_path("scripts"), exe_name)
    if os.path.isfile(scripts_path):
        return scripts_path

    # 2. User scheme scripts directory.
    if sys.version_info >= (3, 10):
        user_scheme = sysconfig.get_preferred_scheme("user")
    elif os.name == "nt":
        user_scheme = "nt_user"
    elif sys.platform == "darwin" and sys._framework:
        user_scheme = "osx_framework_user"
    else:
        user_scheme = "posix_user"

    user_path = os.path.join(
        sysconfig.get_path("scripts", scheme=user_scheme), exe_name
    )
    if os.path.isfile(user_path):
        return user_path

    # 3. bin/ adjacent to package root (pip install --target).
    pkg_root = os.path.dirname(os.path.dirname(__file__))
    target_path = os.path.join(pkg_root, "bin", exe_name)
    if os.path.isfile(target_path):
        return target_path

    # 4. pip build-env overlay detection.
    #
    # Expect to find the binary in <prefix>/pip-build-env-<rand>/overlay/bin/<name>
    # Expect to find a "normal" folder at <prefix>/pip-build-env-<rand>/normal
    #
    # See: https://github.com/pypa/pip/blob/102d8187a1f5a4cd5de7a549fd8a9af34e89a54f/src/pip/_internal/build_env.py#L87
    paths = os.environ.get("PATH", "").split(os.pathsep)
    if len(paths) >= 2:

        def _last_three_parts(path: str) -> list[str]:
            parts = []
            while len(parts) < 3:
                head, tail = os.path.split(path)
                if tail or head != path:
                    parts.append(tail)
                    path = head
                else:
                    parts.append(path)
                    break
            return parts

        maybe_overlay = _last_three_parts(paths[0])
        maybe_normal = _last_three_parts(paths[1])
        if (
            len(maybe_normal) >= 3
            and maybe_normal[-1].startswith("pip-build-env-")
            and maybe_normal[-2] == "normal"
            and len(maybe_overlay) >= 3
            and maybe_overlay[-1].startswith("pip-build-env-")
            and maybe_overlay[-2] == "overlay"
        ):
            candidate = os.path.join(paths[0], exe_name)
            if os.path.isfile(candidate):
                return candidate

    raise FileNotFoundError(
        f"Could not find '{name}' binary. It should be installed as part of "
        f"the agentbeacon package (looked in {scripts_path})."
    )
