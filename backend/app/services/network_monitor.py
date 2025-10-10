"""
网络监控服务
提供网络接口、流量统计、连接信息等功能
"""
import psutil
import time
import logging
import subprocess
import ipaddress
from typing import Dict, Any, List, Optional
from collections import deque
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class NetworkMonitor:
    """网络监控服务"""

    def __init__(self):
        # 流量历史数据缓冲区 (最多保存180个数据点 = 15分钟@5秒)
        self._traffic_buffer = deque(maxlen=180)
        self._last_io_counters = None
        self._last_sample_time = None

    def get_interfaces(self) -> List[Dict[str, Any]]:
        """
        获取所有网络接口信息
        返回: 接口列表，包含名称、IP、MAC、状态、流量统计
        """
        interfaces = []

        try:
            # 获取地址信息
            addrs = psutil.net_if_addrs()
            # 获取统计信息
            stats = psutil.net_if_stats()
            # 获取IO计数
            io_counters = psutil.net_io_counters(pernic=True)

            for iface_name, iface_addrs in addrs.items():
                iface_info = {
                    "name": iface_name,
                    "ipv4": None,
                    "ipv6": None,
                    "mac": None,
                    "is_up": False,
                    "speed": 0,
                    "mtu": 0,
                    "bytes_sent": 0,
                    "bytes_recv": 0,
                    "packets_sent": 0,
                    "packets_recv": 0,
                    "errin": 0,
                    "errout": 0,
                    "dropin": 0,
                    "dropout": 0,
                }

                # 解析地址
                for addr in iface_addrs:
                    if addr.family == psutil.AF_LINK:
                        iface_info["mac"] = addr.address
                    elif addr.family == 2:  # AF_INET (IPv4)
                        iface_info["ipv4"] = addr.address
                    elif addr.family == 10:  # AF_INET6 (IPv6)
                        # 过滤本地链路地址
                        if not addr.address.startswith("fe80"):
                            iface_info["ipv6"] = addr.address

                # 获取状态
                if iface_name in stats:
                    stat = stats[iface_name]
                    iface_info["is_up"] = stat.isup
                    iface_info["speed"] = stat.speed
                    iface_info["mtu"] = stat.mtu

                # 获取流量统计
                if iface_name in io_counters:
                    counter = io_counters[iface_name]
                    iface_info["bytes_sent"] = counter.bytes_sent
                    iface_info["bytes_recv"] = counter.bytes_recv
                    iface_info["packets_sent"] = counter.packets_sent
                    iface_info["packets_recv"] = counter.packets_recv
                    iface_info["errin"] = counter.errin
                    iface_info["errout"] = counter.errout
                    iface_info["dropin"] = counter.dropin
                    iface_info["dropout"] = counter.dropout

                interfaces.append(iface_info)

            return interfaces

        except Exception as e:
            logger.error(f"获取网络接口信息失败: {e}")
            return []

    def sample_traffic(self) -> None:
        """
        采样网络流量数据并存入缓冲区
        应该由后台定时任务每5秒调用一次
        """
        try:
            current_time = time.time()
            current_io = psutil.net_io_counters()

            # 首次采样，无法计算速率
            if self._last_io_counters is None:
                self._last_io_counters = current_io
                self._last_sample_time = current_time
                return

            # 计算时间差
            time_delta = current_time - self._last_sample_time
            if time_delta < 1.0:  # 时间间隔太短
                return

            # 计算速率 (bytes/s)
            upload_bps = (current_io.bytes_sent - self._last_io_counters.bytes_sent) / time_delta
            download_bps = (current_io.bytes_recv - self._last_io_counters.bytes_recv) / time_delta

            # 存入缓冲区
            data_point = {
                "timestamp": int(current_time * 1000),  # 毫秒时间戳
                "upload_bps": max(0, upload_bps),
                "download_bps": max(0, download_bps),
            }
            self._traffic_buffer.append(data_point)

            # 更新状态
            self._last_io_counters = current_io
            self._last_sample_time = current_time

        except Exception as e:
            logger.error(f"采样网络流量失败: {e}")

    def get_traffic_history(self, window: str = "5min") -> Dict[str, Any]:
        """
        获取流量历史数据

        Args:
            window: 时间窗口 ("1min", "5min", "15min")

        Returns:
            包含时间戳和上传/下载速率的数据点列表
        """
        # 确定数据点数量
        window_map = {
            "1min": 12,   # 1分钟 @5秒 = 12个点
            "5min": 60,   # 5分钟 @5秒 = 60个点
            "15min": 180, # 15分钟 @5秒 = 180个点
        }
        max_points = window_map.get(window, 60)

        # 获取最新的N个数据点
        data_points = list(self._traffic_buffer)[-max_points:]

        return {
            "window": window,
            "data_points": data_points,
            "count": len(data_points)
        }

    def get_connections(self) -> Dict[str, Any]:
        """
        获取网络连接信息

        优先使用 /proc/net/tcp 解析(更可靠),回退到 psutil
        """
        connections = []

        try:
            # 尝试使用 psutil 获取连接(可能需要 root 权限)
            conns = psutil.net_connections(kind='inet')

            for conn in conns:
                conn_info = {
                    "fd": conn.fd if hasattr(conn, 'fd') else -1,
                    "family": "IPv4" if conn.family == 2 else "IPv6",
                    "type": "TCP" if conn.type == 1 else "UDP",
                    "local_addr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "-",
                    "remote_addr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "-",
                    "status": conn.status if hasattr(conn, 'status') else "-",
                    "pid": conn.pid if conn.pid else None,
                }

                # 尝试获取进程名
                if conn.pid:
                    try:
                        proc = psutil.Process(conn.pid)
                        conn_info["process"] = proc.name()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        conn_info["process"] = None

                connections.append(conn_info)

            return {
                "success": True,
                "connections": connections,
                "count": len(connections),
                "method": "psutil"
            }

        except (psutil.AccessDenied, PermissionError):
            # 权限不足，返回汇总统计
            logger.warning("无法获取详细连接信息(权限不足)，返回汇总统计")
            return self._get_connection_summary()
        except Exception as e:
            logger.error(f"获取连接信息失败: {e}")
            return self._get_connection_summary()

    def _get_connection_summary(self) -> Dict[str, Any]:
        """
        获取连接汇总统计(无需特殊权限)
        """
        try:
            stats = psutil.net_connections(kind='inet')
            tcp_count = sum(1 for c in stats if c.type == 1)
            udp_count = sum(1 for c in stats if c.type == 2)

            return {
                "success": False,
                "message": "权限不足，仅提供汇总统计",
                "summary": {
                    "tcp_connections": tcp_count,
                    "udp_connections": udp_count,
                    "total": len(stats)
                },
                "method": "summary"
            }
        except Exception as e:
            logger.error(f"获取连接汇总失败: {e}")
            return {
                "success": False,
                "message": f"获取连接信息失败: {str(e)}",
                "summary": {
                    "tcp_connections": 0,
                    "udp_connections": 0,
                    "total": 0
                },
                "method": "fallback"
            }

    def execute_interface_toggle(self, iface: str, enable: bool) -> Dict[str, Any]:
        """
        启用/禁用网络接口

        Args:
            iface: 接口名称
            enable: True=启用, False=禁用

        Returns:
            操作结果
        """
        action = "up" if enable else "down"
        action_text = "启用" if enable else "禁用"

        try:
            # 使用 ip link 命令
            result = subprocess.run(
                ["sudo", "ip", "link", "set", iface, action],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                logger.info(f"成功{action_text}接口: {iface}")
                return {
                    "success": True,
                    "message": f"接口 {iface} 已{action_text}",
                    "interface": iface,
                    "enabled": enable
                }
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"{action_text}接口失败: {iface}, 错误: {error_msg}")
                return {
                    "success": False,
                    "message": f"{action_text}接口失败: {error_msg}",
                    "interface": iface
                }

        except subprocess.TimeoutExpired:
            logger.error(f"{action_text}接口超时: {iface}")
            return {
                "success": False,
                "message": f"{action_text}接口超时",
                "interface": iface
            }
        except Exception as e:
            logger.error(f"{action_text}接口异常: {iface}, {e}")
            return {
                "success": False,
                "message": f"{action_text}接口失败: {str(e)}",
                "interface": iface
            }

    def execute_ip_config(
        self,
        iface: str,
        ip_addr: str,
        netmask: str,
        gateway: Optional[str],
        persistent: bool
    ) -> Dict[str, Any]:
        """
        配置静态IP地址

        Args:
            iface: 接口名称
            ip_addr: IP地址
            netmask: 子网掩码或前缀长度
            gateway: 网关地址(可选)
            persistent: 是否持久化配置

        Returns:
            操作结果
        """
        try:
            # 验证IP地址格式
            try:
                ip_obj = ipaddress.ip_address(ip_addr)
            except ValueError:
                return {
                    "success": False,
                    "message": f"无效的IP地址: {ip_addr}"
                }

            # 转换子网掩码为CIDR前缀
            if netmask.isdigit():
                prefix = int(netmask)
            else:
                try:
                    prefix = ipaddress.ip_network(f"0.0.0.0/{netmask}", strict=False).prefixlen
                except ValueError:
                    return {
                        "success": False,
                        "message": f"无效的子网掩码: {netmask}"
                    }

            cidr = f"{ip_addr}/{prefix}"

            # 临时配置: 使用 ip addr 命令
            logger.info(f"配置接口 {iface}: {cidr}, 网关: {gateway}, 持久化: {persistent}")

            # 删除旧的IP地址(如果存在)
            flush_result = subprocess.run(
                ["sudo", "ip", "addr", "flush", "dev", iface],
                capture_output=True,
                text=True,
                timeout=10
            )

            # 添加新IP地址
            add_result = subprocess.run(
                ["sudo", "ip", "addr", "add", cidr, "dev", iface],
                capture_output=True,
                text=True,
                timeout=10
            )

            if add_result.returncode != 0:
                error_msg = add_result.stderr.strip()
                logger.error(f"添加IP地址失败: {error_msg}")
                return {
                    "success": False,
                    "message": f"添加IP地址失败: {error_msg}"
                }

            # 配置网关
            if gateway:
                # 删除默认网关
                subprocess.run(
                    ["sudo", "ip", "route", "del", "default"],
                    capture_output=True,
                    timeout=10
                )

                # 添加新网关
                gw_result = subprocess.run(
                    ["sudo", "ip", "route", "add", "default", "via", gateway, "dev", iface],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if gw_result.returncode != 0:
                    logger.warning(f"设置网关失败: {gw_result.stderr.strip()}")

            # 持久化配置
            if persistent:
                try:
                    self._write_netplan_config(iface, ip_addr, prefix, gateway)
                except Exception as e:
                    logger.error(f"写入 netplan 配置失败: {e}")
                    return {
                        "success": True,
                        "message": f"IP配置成功(临时生效)，但持久化失败: {str(e)}",
                        "warning": "配置在重启后将丢失"
                    }

            return {
                "success": True,
                "message": f"接口 {iface} 配置成功",
                "interface": iface,
                "ip": cidr,
                "gateway": gateway,
                "persistent": persistent
            }

        except subprocess.TimeoutExpired:
            logger.error(f"配置IP超时: {iface}")
            return {
                "success": False,
                "message": "配置超时"
            }
        except Exception as e:
            logger.error(f"配置IP异常: {e}")
            return {
                "success": False,
                "message": f"配置失败: {str(e)}"
            }

    def _write_netplan_config(self, iface: str, ip_addr: str, prefix: int, gateway: Optional[str]) -> None:
        """写入 netplan 配置文件"""
        netplan_dir = Path("/etc/netplan")
        config_file = netplan_dir / f"99-rosdeck-{iface}.yaml"

        config_content = f"""# RosDeck generated configuration for {iface}
# Generated at {datetime.utcnow().isoformat()}Z
network:
  version: 2
  ethernets:
    {iface}:
      addresses:
        - {ip_addr}/{prefix}
"""

        if gateway:
            config_content += f"""      routes:
        - to: default
          via: {gateway}
"""

        # 写入配置文件
        config_file.write_text(config_content)
        logger.info(f"已写入 netplan 配置: {config_file}")

        # 应用配置
        apply_result = subprocess.run(
            ["sudo", "netplan", "apply"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if apply_result.returncode != 0:
            logger.error(f"netplan apply 失败: {apply_result.stderr}")
            raise Exception(f"netplan apply 失败: {apply_result.stderr}")

        logger.info("netplan 配置已应用")

    def execute_ping(self, target: str, count: int = 4) -> Dict[str, Any]:
        """
        执行 ping 测试

        Args:
            target: 目标主机(IP或域名)
            count: ping 次数

        Returns:
            ping 结果
        """
        try:
            # 验证目标格式(简单检查)
            if not target or len(target) > 255:
                return {
                    "success": False,
                    "message": "无效的目标地址"
                }

            # 执行 ping
            result = subprocess.run(
                ["ping", "-c", str(count), target],
                capture_output=True,
                text=True,
                timeout=30
            )

            return {
                "success": result.returncode == 0,
                "target": target,
                "output": result.stdout,
                "returncode": result.returncode
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "message": "ping 超时",
                "target": target
            }
        except Exception as e:
            logger.error(f"执行 ping 失败: {e}")
            return {
                "success": False,
                "message": f"执行失败: {str(e)}",
                "target": target
            }


# 全局实例
network_monitor = NetworkMonitor()
