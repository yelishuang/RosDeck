"""
Process monitoring service using psutil.
Provides process list retrieval, filtering, sorting, and termination.
"""

import psutil
import logging
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ProcessMonitor:
    """Monitor and manage system processes."""

    @staticmethod
    def get_processes(sort_by: str = "cpu", limit: int = 500) -> List[Dict]:
        """
        Get list of running processes with resource usage.

        Args:
            sort_by: Sort field (cpu, memory, pid, name)
            limit: Maximum number of processes to return

        Returns:
            List of process dictionaries
        """
        processes = []

        try:
            # First pass: trigger cpu_percent calculation
            for proc in psutil.process_iter(['pid']):
                try:
                    proc.cpu_percent(interval=0)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Small delay for cpu_percent to accumulate
            import time
            time.sleep(0.1)

            # Second pass: collect data
            for proc in psutil.process_iter(['pid', 'name', 'username', 'status',
                                            'cpu_percent', 'memory_percent', 'cmdline']):
                try:
                    pinfo = proc.info
                    cmdline = pinfo.get('cmdline', [])
                    cmdline_str = ' '.join(cmdline) if cmdline else pinfo.get('name', '')

                    if len(cmdline_str) > 100:
                        cmdline_str = cmdline_str[:97] + '...'

                    processes.append({
                        'pid': pinfo.get('pid', 0),
                        'name': pinfo.get('name', 'Unknown'),
                        'username': pinfo.get('username', 'Unknown'),
                        'cpu_percent': round(pinfo.get('cpu_percent', 0.0), 1),
                        'memory_percent': round(pinfo.get('memory_percent', 0.0), 1),
                        'status': pinfo.get('status', 'unknown'),
                        'cmdline': cmdline_str
                    })

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                except Exception as e:
                    logger.warning(f"Error reading process: {e}")
                    continue

            # Sort
            sort_map = {
                'cpu': lambda p: p['cpu_percent'],
                'memory': lambda p: p['memory_percent'],
                'pid': lambda p: p['pid'],
                'name': lambda p: p['name'].lower()
            }
            processes.sort(key=sort_map.get(sort_by, sort_map['cpu']), reverse=True)

            return processes[:limit]

        except Exception as e:
            logger.error(f"Error getting processes: {e}")
            return []

    @staticmethod
    def kill_process(pid: int) -> tuple[bool, str]:
        """
        Terminate a process by PID (admin only).

        Args:
            pid: Process ID

        Returns:
            (success: bool, message: str)
        """
        try:
            if pid == 1:
                return False, "Cannot terminate init process"

            if pid == psutil.Process().pid:
                return False, "Cannot terminate self"

            proc = psutil.Process(pid)
            proc_name = proc.name()

            proc.terminate()

            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)

            logger.info(f"Process {pid} ({proc_name}) terminated")
            return True, f"Process {proc_name} (PID {pid}) terminated"

        except psutil.NoSuchProcess:
            return False, f"Process {pid} not found"
        except psutil.AccessDenied:
            return False, f"Permission denied for process {pid}"
        except Exception as e:
            logger.error(f"Error killing process {pid}: {e}")
            return False, str(e)
