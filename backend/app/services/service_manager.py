"""
Systemd service management service.
Provides service list, status, and control operations (start/stop/restart/enable/disable).
"""

import subprocess
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class ServiceManager:
    """Manage systemd services."""

    @staticmethod
    def get_services() -> List[Dict]:
        """
        Get list of systemd services.

        Returns:
            List of service dictionaries with name, status, enabled state
        """
        services = []

        try:
            # List all service units - use --no-legend for faster parsing
            result = subprocess.run(
                ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                logger.error(f"Failed to list services: {result.stderr}")
                return []

            # Parse output
            for line in result.stdout.strip().split('\n'):
                parts = line.split(None, 4)
                if len(parts) >= 4 and parts[0].endswith('.service'):
                    service_name = parts[0].replace('.service', '')
                    load_state = parts[1]
                    active_state = parts[2]
                    sub_state = parts[3]

                    # Skip not-found services
                    if load_state == 'not-found':
                        continue

                    # Don't check enabled state here - too slow
                    # Will check on-demand when needed
                    services.append({
                        'name': service_name,
                        'load': load_state,
                        'active': active_state,
                        'sub': sub_state,
                        'enabled': False,  # Placeholder, check on demand
                        'status': f"{active_state} ({sub_state})"
                    })

            logger.info(f"Retrieved {len(services)} services")
            return services

        except subprocess.TimeoutExpired:
            logger.error("Timeout listing services")
            return []
        except Exception as e:
            logger.error(f"Error getting services: {e}")
            return []

    @staticmethod
    def _is_service_enabled(service_name: str) -> bool:
        """Check if a service is enabled at boot."""
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=3
            )
            return result.stdout.strip() == "enabled"
        except Exception:
            return False

    @staticmethod
    def control_service(service_name: str, action: str) -> tuple[bool, str]:
        """
        Control a systemd service (admin only).

        Args:
            service_name: Name of the service (without .service suffix)
            action: One of: start, stop, restart, enable, disable

        Returns:
            (success: bool, message: str)
        """
        valid_actions = ["start", "stop", "restart", "enable", "disable"]
        if action not in valid_actions:
            return False, f"Invalid action: {action}"

        try:
            result = subprocess.run(
                ["sudo", "systemctl", action, f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                logger.info(f"Service {service_name} {action} successful")
                return True, f"Service {service_name} {action} successful"
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"Service {service_name} {action} failed: {error_msg}")
                return False, f"Failed: {error_msg}"

        except subprocess.TimeoutExpired:
            return False, f"Timeout executing {action} on {service_name}"
        except Exception as e:
            logger.error(f"Error controlling service {service_name}: {e}")
            return False, str(e)

    @staticmethod
    def get_service_status(service_name: str) -> Optional[Dict]:
        """
        Get detailed status of a specific service.

        Args:
            service_name: Name of the service

        Returns:
            Dictionary with service status details or None
        """
        try:
            result = subprocess.run(
                ["systemctl", "status", f"{service_name}.service", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=5
            )

            # Parse status output
            active_result = subprocess.run(
                ["systemctl", "is-active", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=3
            )

            enabled_result = subprocess.run(
                ["systemctl", "is-enabled", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=3
            )

            return {
                'name': service_name,
                'active': active_result.stdout.strip(),
                'enabled': enabled_result.stdout.strip(),
                'status_text': result.stdout
            }

        except Exception as e:
            logger.error(f"Error getting service status {service_name}: {e}")
            return None
