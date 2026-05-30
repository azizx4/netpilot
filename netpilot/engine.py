import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

"""
NetPilot Engine — Experimental user-space traffic control.

Upload: SQM (Fair Queue + CoDel + Token Bucket)
Download: TCP Window Clamping + Rate Policing

المعمارية:
  ┌─────────────────────────────────────────────────────────┐
  │                    NetPilot Engine                       │
  │                                                         │
  │  WinDivert #1          WinDivert #2                     │
  │  "outbound tcp/udp"    "inbound tcp/udp"                │
  │       │                      │                          │
  │       ▼                      ▼                          │
  │  Capture Loop          Download Loop                    │
  │       │                      │                          │
  │  ┌────┴────┐           Token Bucket                     │
  │  │         │           ├─ pass → send                   │
  │  ▼         ▼           └─ drop → TCP يبطّئ              │
  │ Fast-Q   SQM                                            │
  │  │      Queues                                          │
  │  └──┬────┘                                              │
  │     ▼                                                   │
  │  Send Loop                                              │
  └─────────────────────────────────────────────────────────┘

  ICMP ──→ kernel (zero overhead)

Download Policing بمفاهيم CCNA:
- مثل policing بالراوتر (MQC: police rate)
- بدل ما المودم يعبّي الطابور، نحن نسقط الزيادة قبل ما يتكدّس
- TCP يشوف الـ drops ويقلل window → السيرفر يبطّئ الإرسال
- بعد كم RTT، الطابور بالمودم يفضى → latency ينخفض
"""

import time
import logging
import threading
from collections import deque

import psutil

logger = logging.getLogger("netpilot")

from netpilot.sqm import SQMEngine, make_flow_id
from netpilot.config import load_config, get_priority_apps, get_priority_ips


class NetPilotEngine:
    """المحرك الرئيسي v5 — Upload SQM + Download Policing."""

    def __init__(self, bandwidth_kbps: int = 375, download_kbps: int = 0):
        self.sqm = SQMEngine(bandwidth_kbps=bandwidth_kbps)
        self.bandwidth_kbps = bandwidth_kbps

        # Download control (0 = disabled)
        self.download_kbps = download_kbps
        self._dl_rate = download_kbps * 1024  # bytes/sec
        self._dl_tokens = float(8 * 1024)
        self._dl_max_tokens = float(8 * 1024)
        self._dl_last_refill = 0.0
        # Policing = off by default — Window Clamping كافي
        # Policing يضيف overhead (كل داونلود يمر userspace)
        # شغّله بس لو فيه QUIC/UDP traffic كثير
        self._dl_policing_enabled = True  # ضروري — Window Clamping لوحده ما يكفي

        # TCP Window Clamping — proactive download control
        self._dl_window_scale = 8  # default Windows 10/11
        self._dl_max_window = 0    # 0 = disabled
        self._dl_window_clamped = 0
        self._dl_rtt_estimate = 0.12  # 120ms initial
        self._dl_rtt_baseline = 0.0   # أقل RTT مسجّل
        if download_kbps > 0:
            self._calc_max_window()

        # port → PID — atomic swap
        self._port_to_pid: dict[int, int] = {}

        # Priority sets — atomic swap
        self._priority_pids: set[int] = set()
        self._priority_ips: set[str] = set()

        # Fast-path deque
        self._fast_queue: deque = deque()

        # Control
        self._running = False
        self._stop_event = threading.Event()
        self._windivert_ul = None  # upload handle
        self._windivert_dl = None  # download handle
        self._packet_ready = threading.Event()

        # Stats
        self._total_captured = 0
        self._total_sent = 0
        self._fast_path_sent = 0
        self._dl_total = 0
        self._dl_passed = 0
        self._dl_dropped = 0
        self._start_time = 0.0

        # Config
        self._user_config = load_config()
        self._active_priority_names: set[str] = set()

    def update_config(self, config: dict):
        self._user_config = config

    # ══════════════════════════════════════════════
    #  REFRESH STATE
    # ══════════════════════════════════════════════

    def _refresh_state(self):
        _tick = 0
        while not self._stop_event.is_set():
            try:
                _tick += 1

                if _tick % 4 == 1:
                    priority_apps = get_priority_apps(self._user_config)
                    pid_priority: dict[int, str] = {}
                    priority_pids: set[int] = set()
                    active_names: set[str] = set()

                    if priority_apps:
                        for proc in psutil.process_iter(["pid", "name"]):
                            try:
                                name = proc.info["name"]
                                if name is None:
                                    continue
                                name_lower = name.lower()
                                if name_lower in priority_apps:
                                    pid = proc.info["pid"]
                                    pid_priority[pid] = priority_apps[name_lower]
                                    priority_pids.add(pid)
                                    active_names.add(name_lower)
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                continue

                    self.sqm.set_pid_priorities(pid_priority)
                    self._priority_pids = priority_pids
                    self._active_priority_names = active_names

                    priority_ips = get_priority_ips(self._user_config)
                    self._priority_ips = set(priority_ips.keys())
                    self.sqm.set_ip_priorities(priority_ips)

                new_map: dict[int, int] = {}
                for conn in psutil.net_connections(kind="inet"):
                    if conn.laddr and conn.pid:
                        new_map[conn.laddr.port] = conn.pid
                self._port_to_pid = new_map

            except Exception:
                logger.debug("refresh_state error", exc_info=True)

            self._stop_event.wait(0.5)

    # ══════════════════════════════════════════════
    #  UPLOAD: CAPTURE LOOP (existing)
    # ══════════════════════════════════════════════

    def _clamp_window(self, packet):
        """
        TCP Window Clamping — يعدّل الـ window بالـ ACK عشان السيرفر يبطّئ.

        هذا أقرب شي لـ cFosSpeed — بدل ما نسقط الباكت بعد ما وصل،
        نقول للسيرفر "ما أقدر أستقبل أكثر من كذا" فيبطّئ بنفسه.

        - نعدّل outbound ACK packets فقط
        - NIC hardware يحسب الـ checksum تلقائياً (offloading)
        """
        raw = packet.raw

        # Bounds check: minimum IP + TCP header = 40 bytes
        if len(raw) < 40:
            return

        ihl = (raw[0] & 0x0F) * 4
        if ihl < 20 or ihl + 20 > len(raw):
            return

        flags = raw[ihl + 13]

        # SYN packet — نكتشف الـ Window Scale من الـ TCP Options
        if flags & 0x02:
            if ihl + 12 >= len(raw):
                return
            tcp_data_offset = (raw[ihl + 12] >> 4) * 4
            i = ihl + 20  # بعد TCP header الأساسي
            end = min(ihl + tcp_data_offset, len(raw))
            while i < end:
                kind = raw[i]
                if kind == 0:
                    break
                if kind == 1:
                    i += 1
                    continue
                if i + 1 >= end:
                    break
                length = raw[i + 1]
                if length < 2:  # حماية من infinite loop
                    break
                if kind == 3 and length == 3 and i + 2 < end:
                    scale = raw[i + 2]
                    if 0 <= scale <= 14:  # Window Scale valid range
                        self._dl_window_scale = scale
                        self._calc_max_window()
                i += length
            return

        # ACK packet (بس مو SYN-ACK ولا RST)
        if (flags & 0x10) and not (flags & 0x04):
            current_window = (raw[ihl + 14] << 8) | raw[ihl + 15]

            if current_window > self._dl_max_window:
                # Clamp the window
                old_hi = raw[ihl + 14]
                old_lo = raw[ihl + 15]
                new_hi = (self._dl_max_window >> 8) & 0xFF
                new_lo = self._dl_max_window & 0xFF
                raw[ihl + 14] = new_hi
                raw[ihl + 15] = new_lo

                # Incremental TCP checksum update (RFC 1624)
                # بدل ما نحسب الـ checksum كامل، نعدّل الفرق بس
                old_chk = (raw[ihl + 16] << 8) | raw[ihl + 17]
                old_val = (old_hi << 8) | old_lo
                new_val = (new_hi << 8) | new_lo

                # ~old_chk + old_val - new_val
                chk = (~old_chk & 0xFFFF) + old_val - new_val
                while chk < 0:
                    chk += 0xFFFF
                while chk > 0xFFFF:
                    chk = (chk & 0xFFFF) + (chk >> 16)
                chk = ~chk & 0xFFFF

                raw[ihl + 16] = (chk >> 8) & 0xFF
                raw[ihl + 17] = chk & 0xFF

                self._dl_window_clamped += 1

    def _capture_loop(self):
        """Upload capture — Fast-path + SQM + Window Clamping."""
        w = self._windivert_ul
        fast_q = self._fast_queue
        max_window = self._dl_max_window  # cache locally

        while not self._stop_event.is_set():
            try:
                packet = w.recv()
            except Exception:
                if self._stop_event.is_set():
                    break
                continue

            if packet is None:
                continue

            self._total_captured += 1

            # ── TCP Window Clamping (download control) ──
            if packet.tcp and self._dl_max_window > 0:
                self._clamp_window(packet)

            local_port = packet.src_port
            pid = self._port_to_pid.get(local_port) if local_port else None

            # Fast-Path: PID-based
            if pid and pid in self._priority_pids:
                fast_q.append(packet)
                self._packet_ready.set()
                continue

            # SQM Path: IP-based priority or normal
            dst_ip = packet.dst_addr
            ip_pid = None
            if dst_ip and dst_ip in self._priority_ips:
                ip_pid = -1

            flow_id = make_flow_id(packet)
            accepted = self.sqm.enqueue(
                packet=packet,
                flow_id=flow_id,
                pid=ip_pid if ip_pid else pid,
                is_outbound=True,
                size=len(packet.raw),
            )
            if accepted:
                self._packet_ready.set()

    # ══════════════════════════════════════════════
    #  UPLOAD: SEND LOOP (existing)
    # ══════════════════════════════════════════════

    def _send_loop(self):
        """Upload sender — fast-path first, then SQM."""
        w = self._windivert_ul
        fast_q = self._fast_queue

        while not self._stop_event.is_set():
            sent_any = False

            # Fast-path packets first
            while True:
                try:
                    packet = fast_q.popleft()
                except IndexError:
                    break
                try:
                    w.send(packet)
                    self._fast_path_sent += 1
                    self._total_sent += 1
                    sent_any = True
                except Exception:
                    logger.debug("fast-path send error", exc_info=True)

            # SQM packets
            qpkt = self.sqm.dequeue()
            if qpkt is not None:
                delay = self.sqm.consume_tokens(qpkt.size)
                if delay > 0:
                    time.sleep(delay)
                try:
                    w.send(qpkt.packet)
                    self._total_sent += 1
                    sent_any = True
                except Exception:
                    logger.debug("send error", exc_info=True)

            if not sent_any:
                self._packet_ready.wait(timeout=0.005)
                self._packet_ready.clear()

    # ══════════════════════════════════════════════
    #  DOWNLOAD: POLICING (token bucket — burst 8KB)
    # ══════════════════════════════════════════════

    def _download_loop(self):
        """
        Download policing — token bucket pass/drop.
        burst 8KB (بدل 64KB) = أقل spikes.

        ليش policing أحسن من shaping للداونلود:
        - Shaping يأخّر الباكت بالطابور ← يضيف latency
        - Policing يسقط الباكت فوراً ← TCP يبطّئ بسرعة
        - الهدف: نخلّي طابور المودم فاضي، مو نبني طابور عندنا
        """
        w = self._windivert_dl
        self._dl_last_refill = time.monotonic()

        while not self._stop_event.is_set():
            try:
                packet = w.recv()
            except Exception:
                if self._stop_event.is_set():
                    break
                continue

            if packet is None:
                continue

            self._dl_total += 1
            pkt_size = len(packet.raw)

            # Priority bypass
            dst_port = packet.dst_port
            if dst_port:
                pid = self._port_to_pid.get(dst_port)
                if pid and pid in self._priority_pids:
                    try:
                        w.send(packet)
                        self._dl_passed += 1
                    except Exception:
                        pass
                    continue

            src_ip = packet.src_addr
            if src_ip and src_ip in self._priority_ips:
                try:
                    w.send(packet)
                    self._dl_passed += 1
                except Exception:
                    pass
                continue

            # Token bucket — burst 8KB
            now = time.monotonic()
            elapsed = now - self._dl_last_refill
            self._dl_tokens = min(
                self._dl_max_tokens,
                self._dl_tokens + elapsed * self._dl_rate
            )
            self._dl_last_refill = now

            if self._dl_tokens >= pkt_size:
                self._dl_tokens -= pkt_size
                try:
                    w.send(packet)
                    self._dl_passed += 1
                except Exception:
                    pass
            else:
                self._dl_dropped += 1

    # ══════════════════════════════════════════════
    #  RTT MONITOR — adaptive window sizing
    # ══════════════════════════════════════════════

    def _rtt_monitor_loop(self):
        """
        يقيس RTT كل ثانية ويعدّل الـ window تلقائياً.

        بمفاهيم CCNA:
        - مثل IP SLA — نراقب الأداء ونتصرف
        - لما RTT يرتفع = فيه bufferbloat → نصغّر الـ window أكثر
        - لما RTT ينزل = الخط فاضي → نكبّر الـ window شوي
        """
        import subprocess

        # أول قياس لتحديد الـ baseline
        baseline_rtts = []
        for _ in range(3):
            rtt = self._ping_once("8.8.8.8")
            if rtt:
                baseline_rtts.append(rtt)
            if self._stop_event.is_set():
                return

        if baseline_rtts:
            self._dl_rtt_baseline = min(baseline_rtts)
            self._dl_rtt_estimate = self._dl_rtt_baseline
            self._calc_max_window()

        # مراقبة مستمرة — كل 1.5s
        while not self._stop_event.is_set():
            self._stop_event.wait(1.5)
            if self._stop_event.is_set():
                break

            rtt = self._ping_once("8.8.8.8")
            if rtt is None:
                continue

            # تحديث baseline (أقل RTT شفناه)
            if rtt < self._dl_rtt_baseline or self._dl_rtt_baseline == 0:
                self._dl_rtt_baseline = rtt

            # Proportional adaptive: window يتناسب عكسياً مع الازدحام
            # بدل thresholds ثابتة، نستخدم نسبة مستمرة:
            # ratio = baseline / current_rtt → كل ما RTT يرتفع، الـ window ينزل
            ratio = self._dl_rtt_baseline / max(rtt, 0.01)
            ratio = max(min(ratio, 1.0), 0.3)  # clamp: 30%-100%

            self._dl_rtt_estimate = self._dl_rtt_baseline * ratio
            self._calc_max_window()

    def _ping_once(self, target: str) -> float | None:
        """يرسل ping واحد ويرجّع RTT بالثواني."""
        import subprocess
        try:
            result = subprocess.run(
                ["ping", "-n", "1", "-w", "1000", "-4", target],
                capture_output=True, text=True, timeout=3,
            )
            import re
            match = re.search(r"time[=<](\d+)ms", result.stdout)
            if match:
                return int(match.group(1)) / 1000.0
        except Exception:
            pass
        return None

    # ══════════════════════════════════════════════
    #  STATUS & STATS
    # ══════════════════════════════════════════════

    def _status_loop(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(5.0)
            if self._stop_event.is_set():
                break
            self._print_status()

    def _print_status(self):
        elapsed = time.time() - self._start_time
        stats = self.sqm.get_stats()

        print(f"\n--- Status @ {elapsed:.0f}s ---")

        if self._active_priority_names:
            print(f"  Active priority apps: {', '.join(self._active_priority_names)}")

        print(f"  Fast-path sent: {self._fast_path_sent}")

        for name in ("Voice", "High", "Normal", "Bulk"):
            s = stats[name]
            if s["sent"] > 0 or s["queue_size"] > 0:
                drop_info = f", dropped: {s['dropped']}" if s['dropped'] > 0 else ""
                codel = " [CoDel DROPPING]" if s["codel_dropping"] else ""
                print(f"  {name:6s}: sent {s['sent']:6d} ({s['bytes_sent']/1024:.0f} KB), queue: {s['queue_size']}{drop_info}{codel}")

        total = stats["total"]
        print(f"  Upload: sent {total['sent']}, dropped {total['dropped']} ({total['drop_rate_pct']:.1f}%)")

        if self.download_kbps > 0:
            dl_drop_pct = (self._dl_dropped / max(self._dl_total, 1)) * 100
            print(f"  Download: total {self._dl_total}, passed {self._dl_passed}, dropped {self._dl_dropped} ({dl_drop_pct:.1f}%)")
            print(f"  Window: clamped {self._dl_window_clamped}, max_field={self._dl_max_window}, RTT={self._dl_rtt_estimate*1000:.0f}ms, baseline={self._dl_rtt_baseline*1000:.0f}ms")

    def get_stats(self) -> dict:
        stats = self.sqm.get_stats()
        stats["active_priority_names"] = self._active_priority_names
        stats["captured"] = self._total_captured
        stats["fast_path_sent"] = self._fast_path_sent
        stats["bandwidth_kbps"] = self.bandwidth_kbps
        stats["download_kbps"] = self.download_kbps
        stats["dl_total"] = self._dl_total
        stats["dl_passed"] = self._dl_passed
        stats["dl_dropped"] = self._dl_dropped
        stats["dl_window_clamped"] = self._dl_window_clamped
        return stats

    def set_bandwidth(self, kbps: int):
        self.bandwidth_kbps = kbps
        self.sqm.set_bandwidth(kbps)

    def _calc_max_window(self):
        """
        يحسب الـ max TCP window field بناءً على سرعة الداونلود.

        بمفاهيم CCNA:
        BDP (Bandwidth-Delay Product) = سرعة × RTT
        Window = BDP × factor / 2^scale

        factor = 0.5 → نسمح بنصف الـ BDP فقط
        هذا يخلّي طابور المودم ما يمتلئ أبداً
        (أقرب لـ cFosSpeed الي يستخدم kernel-level shaping)
        """
        if self.download_kbps <= 0:
            self._dl_max_window = 0
            return
        rate_bytes = self.download_kbps * 1024
        rtt = self._dl_rtt_estimate
        factor = 0.5  # 50% من BDP — aggressive عشان نمنع الـ bufferbloat
        bdp = rate_bytes * rtt * factor
        self._dl_max_window = int(bdp / (2 ** self._dl_window_scale))
        self._dl_max_window = max(min(self._dl_max_window, 65535), 128)

    def set_download_bandwidth(self, kbps: int):
        """يحدّث سرعة الداونلود. 0 = إيقاف."""
        self.download_kbps = kbps
        self._dl_rate = kbps * 1024
        self._calc_max_window()

    # ══════════════════════════════════════════════
    #  START / STOP
    # ══════════════════════════════════════════════

    def run(self):
        try:
            import pydivert
        except ImportError:
            print("Error: pydivert not installed")
            return

        print("=" * 55)
        print("  NetPilot — Traffic Control PoC")
        print("=" * 55)
        print(f"  Upload limit:   {self.bandwidth_kbps} KB/s")
        print(f"  Download limit: {self.download_kbps} KB/s" + (" (disabled)" if self.download_kbps == 0 else ""))
        print(f"  Status: Experimental — not production-ready")
        print(f"  Press Ctrl+C to stop")
        print("=" * 55)

        try:
            w_ul = pydivert.WinDivert("outbound and (tcp or udp)")
            w_ul.open()
            self._windivert_ul = w_ul

            if self.download_kbps > 0 and self._dl_policing_enabled:
                w_dl = pydivert.WinDivert("inbound and (tcp or udp)")
                w_dl.open()
                self._windivert_dl = w_dl
        except Exception as e:
            print(f"\nError: {e}")
            print("Run as Administrator.")
            return

        self._running = True
        self._stop_event.clear()
        self._start_time = time.time()

        threads = [
            threading.Thread(target=self._refresh_state, daemon=True, name="refresh"),
            threading.Thread(target=self._capture_loop, daemon=True, name="capture"),
            threading.Thread(target=self._send_loop, daemon=True, name="send"),
            threading.Thread(target=self._status_loop, daemon=True, name="status"),
        ]

        if self.download_kbps > 0 and self._dl_policing_enabled:
            threads.append(
                threading.Thread(target=self._download_loop, daemon=True, name="download")
            )

        if self.download_kbps > 0:
            threads.append(
                threading.Thread(target=self._rtt_monitor_loop, daemon=True, name="rtt-monitor")
            )

        for t in threads:
            t.start()

        print("  Engine running...\n")

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(1.0)
        except KeyboardInterrupt:
            print("\n\nStopping...")
        finally:
            self._stop_event.set()
            self._running = False
            time.sleep(0.5)
            w_ul.close()
            if self._windivert_dl:
                self._windivert_dl.close()
            self._print_status()
            print("\nNetPilot stopped.")

    def start_background(self):
        import pydivert

        w_ul = pydivert.WinDivert("outbound and (tcp or udp)")
        w_ul.open()
        self._windivert_ul = w_ul

        if self.download_kbps > 0 and self._dl_policing_enabled:
            w_dl = pydivert.WinDivert("inbound and (tcp or udp)")
            w_dl.open()
            self._windivert_dl = w_dl

        self._running = True
        self._stop_event.clear()
        self._start_time = time.time()

        threads = [
            threading.Thread(target=self._refresh_state, daemon=True, name="refresh"),
            threading.Thread(target=self._capture_loop, daemon=True, name="capture"),
            threading.Thread(target=self._send_loop, daemon=True, name="send"),
        ]

        if self.download_kbps > 0 and self._dl_policing_enabled:
            threads.append(
                threading.Thread(target=self._download_loop, daemon=True, name="download")
            )

        if self.download_kbps > 0:
            threads.append(
                threading.Thread(target=self._rtt_monitor_loop, daemon=True, name="rtt-monitor")
            )

        for t in threads:
            t.start()

    def stop(self):
        self._stop_event.set()
        self._running = False
        time.sleep(0.3)
        if self._windivert_ul:
            try:
                self._windivert_ul.close()
            except Exception:
                pass
        if self._windivert_dl:
            try:
                self._windivert_dl.close()
            except Exception:
                pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="NetPilot — Traffic Control PoC")
    parser.add_argument("-r", "--rate", type=int, default=375, help="Upload limit KB/s")
    parser.add_argument("-d", "--download", type=int, default=0, help="Download limit KB/s (0=off)")
    args = parser.parse_args()

    engine = NetPilotEngine(bandwidth_kbps=args.rate, download_kbps=args.download)
    engine.run()


if __name__ == "__main__":
    main()
