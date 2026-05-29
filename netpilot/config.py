"""
Config — حفظ وتحميل إعدادات المستخدم.

يحفظ:
- قائمة البرامج وأولوياتها (very_high / high)
- قائمة IPs وأولوياتها
- سرعة الـ bandwidth

الملف: netpilot_config.json بنفس مجلد البرنامج
"""

import json
import os
import re

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "netpilot_config.json")

# الأولويات المتاحة
PRIORITY_VERY_HIGH = "very_high"   # → Voice tin (أعلى أولوية)
PRIORITY_HIGH = "high"             # → Game tin
PRIORITY_NORMAL = "normal"         # → Normal tin (الافتراضي)

DEFAULT_BANDWIDTH = 375
DEFAULT_DOWNLOAD_BANDWIDTH = 0  # 0 = disabled

# Regex لفحص IP
_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def is_ip(value: str) -> bool:
    """يشيك هل القيمة IP address."""
    return bool(_IP_RE.match(value))


def _make_default() -> dict:
    return {
        "bandwidth_kbps": DEFAULT_BANDWIDTH,
        "download_kbps": DEFAULT_DOWNLOAD_BANDWIDTH,
        "apps": {},
        "ips": {},
    }


def load_config() -> dict:
    """يحمّل الإعدادات من الملف. لو ما فيه ملف يرجّع الافتراضي."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            if "apps" not in config:
                config["apps"] = {}
            if "ips" not in config:
                config["ips"] = {}
            if "bandwidth_kbps" not in config:
                config["bandwidth_kbps"] = DEFAULT_BANDWIDTH
            if "download_kbps" not in config:
                config["download_kbps"] = DEFAULT_DOWNLOAD_BANDWIDTH
            return config
    except (FileNotFoundError, json.JSONDecodeError):
        return _make_default()


def save_config(config: dict):
    """يحفظ الإعدادات للملف."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def add_app(config: dict, exe_name: str, priority: str) -> dict:
    """يضيف برنامج بأولوية معيّنة."""
    exe_name = exe_name.lower().strip()
    if not exe_name.endswith(".exe"):
        exe_name += ".exe"
    config["apps"][exe_name] = priority
    save_config(config)
    return config


def remove_app(config: dict, exe_name: str) -> dict:
    """يشيل برنامج من القائمة."""
    exe_name = exe_name.lower().strip()
    if not exe_name.endswith(".exe"):
        exe_name += ".exe"
    config["apps"].pop(exe_name, None)
    save_config(config)
    return config


def add_ip(config: dict, ip_addr: str, priority: str) -> dict:
    """يضيف IP بأولوية معيّنة."""
    ip_addr = ip_addr.strip()
    config["ips"][ip_addr] = priority
    save_config(config)
    return config


def remove_ip(config: dict, ip_addr: str) -> dict:
    """يشيل IP من القائمة."""
    ip_addr = ip_addr.strip()
    config["ips"].pop(ip_addr, None)
    save_config(config)
    return config


def get_app_priority(config: dict, exe_name: str) -> str:
    exe_name = exe_name.lower().strip()
    return config["apps"].get(exe_name, PRIORITY_NORMAL)


def get_priority_apps(config: dict) -> dict[str, str]:
    """يرجّع كل البرامج ذات الأولوية (very_high و high بس)."""
    return {k: v for k, v in config["apps"].items() if v != PRIORITY_NORMAL}


def get_priority_ips(config: dict) -> dict[str, str]:
    """يرجّع كل الـ IPs ذات الأولوية."""
    return dict(config.get("ips", {}))
