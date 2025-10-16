import logging
import os
import subprocess

logger = logging.getLogger(__name__)

AUTH_HELPER_PATH = os.environ.get(
    "ROSDECK_AUTH_HELPER",
    "/usr/local/libexec/rosdeck-auth-helper"
)

CONTROL_HELPER_DEFAULT = "/usr/local/libexec/rosdeck-control-helper"
CONTROL_HELPER_CANDIDATES = [
    os.environ.get("ROSDECK_CONTROL_HELPER"),
    os.environ.get("ROSDECK_POWER_HELPER"),  # Legacy environment variable support.
    CONTROL_HELPER_DEFAULT,
    "/usr/local/libexec/rosdeck-power-helper",  # Legacy helper path.
]


def _run_helper(password: str, username: str = "root") -> bool:
    if not password:
        logger.warning("Empty password provided to auth helper")
        return False

    if not os.path.exists(AUTH_HELPER_PATH):
        logger.error("Auth helper missing at %s", AUTH_HELPER_PATH)
        return False

    try:
        completed = subprocess.run(
            [AUTH_HELPER_PATH, "--user", username],
            input=(password + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        logger.error("Auth helper not found: %s", AUTH_HELPER_PATH)
        return False
    except Exception as exc:
        logger.exception("Auth helper invocation failed: %s", exc)
        return False

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="ignore").strip()
        if stderr:
            logger.warning("Auth helper rejected password: %s", stderr)
        else:
            logger.warning(
                "Auth helper exited with code %s for user %s",
                completed.returncode,
                username,
            )
        return False

    return True


def verify_root_password(password: str) -> bool:
    """
    Validate the root password via the privileged helper binary.
    """
    return _run_helper(password, username="root")


def execute_power_action(action: str) -> bool:
    allowed = {"reboot", "shutdown"}
    if action not in allowed:
        logger.error("Unsupported power action: %s", action)
        return False

    helper_path = next(
        (path for path in CONTROL_HELPER_CANDIDATES if path and os.path.exists(path)),
        CONTROL_HELPER_DEFAULT,
    )

    if not helper_path or not os.path.exists(helper_path):
        logger.error(
            "System control helper missing. Checked paths: %s",
            ", ".join(filter(None, CONTROL_HELPER_CANDIDATES)),
        )
        return False

    try:
        completed = subprocess.run(
            [helper_path, action],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        logger.error("System control helper not found: %s", helper_path)
        return False
    except Exception as exc:
        logger.exception("System control helper invocation failed: %s", exc)
        return False

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="ignore").strip()
        if stderr:
            logger.error(
                "System control helper returned %s: %s",
                completed.returncode,
                stderr,
            )
        else:
            logger.error(
                "System control helper returned non-zero exit code %s",
                completed.returncode,
            )
        return False

    return True
