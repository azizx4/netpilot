import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

"""
مراقبة الباكتات وربطها بالبرامج.

كيف يشتغل:
1. WinDivert يعترض كل الباكتات (TCP/UDP)
2. من كل باكت ناخذ الـ port (المصدر أو الوجهة)
3. psutil يقول لنا أي PID ماسك هالـ port
4. نقارن الـ PID بقائمة البرامج ذات الأولوية

بمفاهيم CCNA:
- WinDivert يشتغل مثل SPAN port — ينسخ/يعترض الترافك
- ربط الباكت بالـ PID يشبه MAC address table — نعرف مين أرسل وش
- الفلتر "tcp or udp" يشبه ACL — نحدد وش نبي نشوفه
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field

import psutil

from netpilot.process_detector import detect_running_apps, DetectedApp


@dataclass
class AppTrafficStats:
    """إحصائيات ترافك لبرنامج واحد."""
    app: DetectedApp
    packets_out: int = 0     # باكتات طالعة (upload)
    packets_in: int = 0      # باكتات داخلة (download)
    bytes_out: int = 0       # بايتات طالعة
    bytes_in: int = 0        # بايتات داخلة


def build_port_to_pid_map() -> dict[int, int]:
    """
    يبني جدول: port → PID

    يشبه CAM table بالسويتش — بس بدل MAC→port، عندنا port→PID.
    نستخدمه عشان لما نشوف باكت على port معيّن، نعرف أي برنامج أرسله.
    """
    mapping: dict[int, int] = {}
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr and conn.pid:
            mapping[conn.laddr.port] = conn.pid
    return mapping


def monitor_traffic(duration_seconds: int = 10, refresh_interval: float = 2.0):
    """
    يراقب الترافك لمدة محددة ويطبع الإحصائيات.

    هذي المرحلة مراقبة فقط — ما نتحكم بشي.
    الهدف: نتأكد إننا نقدر نمسك الباكتات ونربطها بالبرامج صح.

    يتطلب صلاحيات Administrator لأن WinDivert يحتاج يتعامل مع الـ driver.
    """
    try:
        import pydivert
    except ImportError:
        print("خطأ: pydivert مو مثبت. ثبته بـ: pip install pydivert")
        return

    # --- اكتشاف البرامج ---
    priority_apps = detect_running_apps()
    if not priority_apps:
        print("ما لقيت أي برنامج ذو أولوية شغال.")
        print("شغّل برنامج ذو أولوية وجرب مرة ثانية.")
        return

    print("برامج مكتشفة ذات أولوية:")
    for app in priority_apps:
        print(f"  [{app.pid}] {app.display_name} ({app.app_type})")
    print()

    priority_pids = {app.pid for app in priority_apps}
    pid_to_app = {app.pid: app for app in priority_apps}

    # --- إحصائيات ---
    stats: dict[int, AppTrafficStats] = {}
    other_packets = 0
    other_bytes = 0

    print(f"بدأت المراقبة لمدة {duration_seconds} ثانية...")
    print("(يتطلب صلاحيات Administrator)\n")

    # فلتر: نبي كل TCP و UDP
    # مثل ACL permit tcp any any + permit udp any any
    try:
        w = pydivert.WinDivert("tcp or udp")
        w.open()
    except Exception as e:
        print(f"خطأ فتح WinDivert: {e}")
        print("تأكد إنك مشغل كـ Administrator.")
        return

    start_time = time.time()
    last_print = start_time

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= duration_seconds:
                break

            # نحدّث جدول port→PID كل فترة
            # (البرامج تفتح وتسكر connections باستمرار)
            if time.time() - last_print >= refresh_interval:
                port_map = build_port_to_pid_map()
                _print_stats(stats, other_packets, other_bytes, elapsed)
                last_print = time.time()
            elif elapsed < 0.1:
                port_map = build_port_to_pid_map()

            # نقرأ باكت (مع timeout عشان ما يعلّق)
            try:
                packet = w.recv()
            except Exception:
                continue

            if packet is None:
                continue

            # مهم: نرجّع الباكت فوراً — إحنا نراقب بس، ما نوقف شي
            w.send(packet)

            # نحدد الـ port المحلي
            # لو الباكت طالع (outbound): المصدر هو المحلي
            # لو داخل (inbound): الوجهة هو المحلي
            local_port = None
            is_outbound = packet.is_outbound

            if is_outbound and packet.src_port:
                local_port = packet.src_port
            elif not is_outbound and packet.dst_port:
                local_port = packet.dst_port

            if local_port is None:
                continue

            # نشوف أي PID ماسك هالـ port
            pid = port_map.get(local_port)
            packet_len = len(packet.raw)

            if pid and pid in priority_pids:
                if pid not in stats:
                    stats[pid] = AppTrafficStats(app=pid_to_app[pid])

                if is_outbound:
                    stats[pid].packets_out += 1
                    stats[pid].bytes_out += packet_len
                else:
                    stats[pid].packets_in += 1
                    stats[pid].bytes_in += packet_len
            else:
                other_packets += 1
                other_bytes += packet_len

    except KeyboardInterrupt:
        print("\nتم إيقاف المراقبة.")
    finally:
        w.close()

    # طباعة النتائج النهائية
    print("\n" + "=" * 50)
    print("النتائج النهائية:")
    print("=" * 50)
    _print_stats(stats, other_packets, other_bytes, time.time() - start_time)


def _print_stats(
    stats: dict[int, AppTrafficStats],
    other_packets: int,
    other_bytes: int,
    elapsed: float,
):
    """يطبع الإحصائيات الحالية."""
    print(f"--- بعد {elapsed:.1f} ثانية ---")

    if stats:
        for pid, s in stats.items():
            total_pkts = s.packets_out + s.packets_in
            total_kb = (s.bytes_out + s.bytes_in) / 1024
            print(
                f"  {s.app.display_name:20s} | "
                f"out: {s.packets_out:5d} pkts ({s.bytes_out/1024:7.1f} KB) | "
                f"in: {s.packets_in:5d} pkts ({s.bytes_in/1024:7.1f} KB) | "
                f"total: {total_pkts:5d} pkts ({total_kb:7.1f} KB)"
            )
    else:
        print("  (ما سجّلنا ترافك من البرامج ذات الأولوية بعد)")

    other_kb = other_bytes / 1024
    print(f"  {'باقي البرامج':20s} | {other_packets:5d} pkts ({other_kb:7.1f} KB)")
    print()


# ---------- تشغيل مباشر ----------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NetPilot — مراقبة الباكتات")
    parser.add_argument(
        "-t", "--time",
        type=int,
        default=15,
        help="مدة المراقبة بالثواني (افتراضي: 15)",
    )
    args = parser.parse_args()

    monitor_traffic(duration_seconds=args.time)
