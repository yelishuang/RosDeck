"""
journalctl 日志读取服务
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


PRIORITY_LABELS = [
    (0, "EMERG"),
    (1, "ALERT"),
    (2, "CRIT"),
    (3, "ERR"),
    (4, "WARNING"),
    (5, "NOTICE"),
    (6, "INFO"),
    (7, "DEBUG"),
]

PRIORITY_NAME_TO_CODE = {label.lower(): code for code, label in PRIORITY_LABELS}


class JournalctlError(Exception):
    """Wrapper for journalctl invocation errors."""

    def __init__(self, message: str, code: str = "JOURNALCTL_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class JournalEntry:
    cursor: str
    timestamp: Optional[str]
    priority: Optional[str]
    priority_code: Optional[int]
    message: str
    hostname: Optional[str]
    unit: Optional[str]
    pid: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cursor": self.cursor,
            "timestamp": self.timestamp,
            "priority": self.priority,
            "priority_code": self.priority_code,
            "message": self.message,
            "hostname": self.hostname,
            "unit": self.unit,
            "pid": self.pid,
        }


class JournalctlReader:
    """Encapsulates journalctl command execution and parsing."""

    def __init__(
        self,
        binary: str = "journalctl",
        timeout_seconds: int = 5,
        default_limit_user: int = 100,
        max_limit_user: int = 200,
        default_limit_admin: int = 400,
        max_limit_admin: int = 1000,
    ):
        self.binary = binary
        self.timeout = timeout_seconds
        self.default_limit_user = default_limit_user
        self.max_limit_user = max_limit_user
        self.default_limit_admin = default_limit_admin
        self.max_limit_admin = max_limit_admin

    # --------- Public API -------------------------------------------------

    def query(
        self,
        *,
        is_admin: bool,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        priority: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute journalctl and return structured log entries.
        Always returns entries in从新到旧顺序。
        """
        effective_limit = self._resolve_limit(limit, is_admin)
        cmd, drop_first = self._build_command(
            is_admin=is_admin,
            limit=effective_limit,
            cursor=cursor,
            since=since,
            until=until,
            priority=priority,
            keyword=keyword,
        )

        raw_lines = self._run_command(cmd)

        entries = self._parse_entries(raw_lines)
        if drop_first and cursor:
            entries = [entry for entry in entries if entry.cursor != cursor]

        has_more = len(entries) > effective_limit
        entries = entries[:effective_limit]

        next_cursor = entries[-1].cursor if entries else cursor

        return {
            "entries": [entry.to_dict() for entry in entries],
            "has_more": has_more,
            "next_cursor": next_cursor,
            "limit": effective_limit,
        }

    def probe_access(self, *, is_admin: bool) -> Tuple[bool, Optional[str]]:
        """Check whether current user can read journal."""
        try:
            self.query(is_admin=is_admin, limit=1)
            return True, None
        except JournalctlError as exc:
            return False, exc.message

    # --------- Helpers ----------------------------------------------------

    def _resolve_limit(self, limit: Optional[int], is_admin: bool) -> int:
        if is_admin:
            default = self.default_limit_admin
            maximum = self.max_limit_admin
        else:
            default = self.default_limit_user
            maximum = self.max_limit_user

        if limit is None:
            return default

        try:
            numeric = int(limit)
        except (TypeError, ValueError):
            numeric = default

        return max(1, min(numeric, maximum))

    def _build_command(
        self,
        *,
        is_admin: bool,
        limit: int,
        cursor: Optional[str],
        since: Optional[str],
        until: Optional[str],
        priority: Optional[str],
        keyword: Optional[str],
    ) -> Tuple[List[str], bool]:
        cmd = [
            self.binary,
            "--output=json",
            "--no-pager",
            "--reverse",
            "-n",
            str(limit + 1),
        ]

        drop_first = False

        if cursor:
            cmd += ["--cursor", cursor]
            drop_first = True

        if since:
            formatted = self._format_time(since)
            if formatted:
                cmd += ["--since", formatted]

        if until:
            formatted = self._format_time(until)
            if formatted:
                cmd += ["--until", formatted]

        priority_clause = self._build_priority(priority, is_admin=is_admin)
        if priority_clause:
            cmd += ["-p", priority_clause]

        if keyword:
            cmd += [f"--grep={keyword}"]

        return cmd, drop_first

    def _build_priority(self, value: Optional[str], *, is_admin: bool) -> Optional[str]:
        if not value:
            # For普通用户未指定级别时，默认展示 INFO 及以上
            return "0..7" if is_admin else "0..6"

        token = value.strip().lower()
        if ".." in token:
            start, end = token.split("..", 1)
            start_code = self._priority_to_code(start)
            end_code = self._priority_to_code(end)
            if start_code is None or end_code is None:
                raise JournalctlError("无效的日志级别范围", code="INVALID_PRIORITY")
            lower = min(start_code, end_code)
            upper = max(start_code, end_code)
        else:
            code = self._priority_to_code(token)
            if code is None:
                raise JournalctlError("无效的日志级别", code="INVALID_PRIORITY")
            lower = upper = code

        if not is_admin:
            upper = min(upper, 6)  # 普通用户最多到 INFO

        return f"{lower}..{upper}"

    def _priority_to_code(self, token: str) -> Optional[int]:
        if token.isdigit():
            try:
                numeric = int(token)
            except ValueError:
                return None
            return numeric if 0 <= numeric <= 7 else None
        return PRIORITY_NAME_TO_CODE.get(token.lower())

    def _run_command(self, cmd: List[str]) -> List[str]:
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise JournalctlError(
                "journalctl 命令不可用，请确认系统使用 systemd 并已安装",
                code="JOURNALCTL_NOT_FOUND",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise JournalctlError(
                "读取日志超时，请缩小筛选范围或降低行数限制",
                code="JOURNALCTL_TIMEOUT",
            ) from exc

        if completed.returncode != 0:
            stderr = completed.stderr.strip() or "journalctl 调用失败"
            raise JournalctlError(stderr, code="JOURNALCTL_FAILED")

        lines = [
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip()
        ]
        return lines

    def _parse_entries(self, lines: List[str]) -> List[JournalEntry]:
        entries: List[JournalEntry] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("无法解析的 journalctl 行: %s", line)
                continue

            cursor = payload.get("__CURSOR")
            if not cursor:
                continue

            timestamp_iso = self._format_realtime_timestamp(
                payload.get("__REALTIME_TIMESTAMP")
            )

            priority_code = self._safe_int(payload.get("PRIORITY"))
            priority_name = (
                PRIORITY_LABELS[priority_code][1]
                if priority_code is not None and 0 <= priority_code <= 7
                else None
            )

            unit = (
                payload.get("_SYSTEMD_UNIT")
                or payload.get("SYSLOG_IDENTIFIER")
                or payload.get("_COMM")
            )

            entry = JournalEntry(
                cursor=cursor,
                timestamp=timestamp_iso,
                priority=priority_name,
                priority_code=priority_code,
                message=payload.get("MESSAGE", ""),
                hostname=payload.get("_HOSTNAME"),
                unit=unit,
                pid=payload.get("_PID"),
            )
            entries.append(entry)

        return entries

    def _format_realtime_timestamp(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            micros = int(value)
        except (TypeError, ValueError):
            return None
        seconds = micros / 1_000_000
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    def _format_time(self, value: str) -> Optional[str]:
        """Accept ISO-like string and convert to 'YYYY-MM-DD HH:MM:SS'."""
        token = value.strip()
        if not token:
            return None
        try:
            if token.endswith("Z"):
                dt = datetime.fromisoformat(token[:-1]).replace(tzinfo=timezone.utc)
            else:
                dt = datetime.fromisoformat(token)
        except ValueError:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def priorities() -> List[Dict[str, Any]]:
        return [
            {"value": str(code), "name": name, "label": f"{code} {name}"}
            for code, name in PRIORITY_LABELS
        ]

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


journalctl_reader = JournalctlReader()

