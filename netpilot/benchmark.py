"""
NetPilot Benchmark — تقييم شامل لجاهزية البرنامج.

يقيس:
1. Baseline latency (بدون حمل)
2. Latency under upload stress (bufferbloat)
3. Jitter (تذبذب التأخير)
4. Packet loss
5. المقارنة: مع وبدون NetPilot

بمفاهيم CCNA:
- Latency = RTT (Round Trip Time)
- Jitter = التغير في التأخير بين الباكتات المتتالية
- Packet Loss = نسبة الباكتات الضائعة
- Bufferbloat = ارتفاع RTT بسبب طوابير ممتلئة عند المودم
"""

import subprocess
import re
import time
import threading
import socket
import statistics
import sys
import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


# ── Targets ──────────────────────────────────────────
TARGETS = {
    "Discord":    "162.159.138.232",
    "Cloudflare": "1.1.1.1",
    "Google DNS": "8.8.8.8",
}

PING_COUNT = 20


def parse_ping_results(output: str) -> list[float]:
    """يستخرج أوقات الـ RTT من output ويندوز."""
    times = []
    for line in output.splitlines():
        match = re.search(r"time[=<](\d+)ms", line)
        if match:
            times.append(float(match.group(1)))
    return times


def run_ping(target_ip: str, count: int = 20) -> list[float]:
    """يرسل ping ويرجّع قائمة الأوقات."""
    try:
        result = subprocess.run(
            ["ping", "-n", str(count), "-4", target_ip],
            capture_output=True, text=True, timeout=count * 2 + 10,
        )
        return parse_ping_results(result.stdout)
    except Exception:
        return []


def calc_jitter(times: list[float]) -> float:
    """
    يحسب الـ Jitter — معدل الفرق بين كل قياسين متتاليين.
    مهم للتطبيقات الحساسة للتأخير — jitter عالي = أداء غير مستقر.
    """
    if len(times) < 2:
        return 0.0
    diffs = [abs(times[i+1] - times[i]) for i in range(len(times)-1)]
    return statistics.mean(diffs)


def calc_percentile(times: list[float], p: float) -> float:
    """P95/P99 — أسوأ الحالات (tail latency)."""
    if not times:
        return 0.0
    sorted_t = sorted(times)
    idx = int(len(sorted_t) * p / 100)
    idx = min(idx, len(sorted_t) - 1)
    return sorted_t[idx]


def upload_flood(duration: int, stop_event: threading.Event):
    """ابلود ثقيل لتشبيع الخط."""
    threads = []

    def _udp():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        data = b"\x00" * (64 * 1024)
        while not stop_event.is_set():
            try:
                sock.sendto(data, ("9.9.9.9", 9999))
            except:
                pass
        sock.close()

    def _tcp():
        data = b"\x00" * (128 * 1024)
        while not stop_event.is_set():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect(("9.9.9.9", 80))
                while not stop_event.is_set():
                    try:
                        s.send(data)
                    except:
                        break
                s.close()
            except:
                if not stop_event.is_set():
                    time.sleep(0.2)

    for _ in range(4):
        t = threading.Thread(target=_udp, daemon=True)
        t.start()
        threads.append(t)
    for _ in range(3):
        t = threading.Thread(target=_tcp, daemon=True)
        t.start()
        threads.append(t)

    return threads


def print_header(text: str):
    width = 60
    print()
    print("=" * width)
    print(f"  {text}")
    print("=" * width)


def print_results(name: str, times: list[float]):
    """يطبع نتائج قياس واحد بشكل مفصّل."""
    if not times:
        print(f"  {name}: FAILED (0 replies)")
        return

    avg = statistics.mean(times)
    mn = min(times)
    mx = max(times)
    jitter = calc_jitter(times)
    p95 = calc_percentile(times, 95)
    p99 = calc_percentile(times, 99)
    loss_pct = (1 - len(times) / PING_COUNT) * 100
    stdev = statistics.stdev(times) if len(times) > 1 else 0

    print(f"  {name}:")
    print(f"    Min/Avg/Max  = {mn:.0f} / {avg:.0f} / {mx:.0f} ms")
    print(f"    P95 / P99    = {p95:.0f} / {p99:.0f} ms")
    print(f"    Jitter       = {jitter:.1f} ms")
    print(f"    StdDev       = {stdev:.1f} ms")
    print(f"    Packet Loss  = {loss_pct:.0f}%")
    print(f"    Samples      = {len(times)}/{PING_COUNT}")

    # تقييم
    grades = []
    if avg < 50:
        grades.append("Latency: EXCELLENT")
    elif avg < 100:
        grades.append("Latency: GOOD")
    elif avg < 150:
        grades.append("Latency: FAIR")
    else:
        grades.append("Latency: POOR")

    if jitter < 5:
        grades.append("Jitter: EXCELLENT")
    elif jitter < 15:
        grades.append("Jitter: GOOD")
    elif jitter < 30:
        grades.append("Jitter: FAIR")
    else:
        grades.append("Jitter: POOR")

    print(f"    Grade        = {' | '.join(grades)}")


def run_benchmark(label: str) -> dict:
    """يشغّل اختبار كامل (baseline + under load) ويرجّع النتائج."""
    results = {}

    # ── Phase 1: Baseline (no load) ──
    print_header(f"PHASE 1: Baseline Latency ({label})")
    print("  Pinging targets with no upload load...\n")

    for name, ip in TARGETS.items():
        times = run_ping(ip, PING_COUNT)
        results[f"{name}_baseline"] = times
        print_results(name, times)

    # ── Phase 2: Under upload stress ──
    print_header(f"PHASE 2: Under Upload Stress ({label})")
    print("  Starting upload flood (4 UDP + 3 TCP)...")

    stop_event = threading.Event()
    flood_threads = upload_flood(60, stop_event)

    # ننتظر 3 ثواني عشان الضغط يوصل ذروته
    time.sleep(3)
    print("  Flood active. Pinging...\n")

    for name, ip in TARGETS.items():
        times = run_ping(ip, PING_COUNT)
        results[f"{name}_loaded"] = times
        print_results(name, times)

    # نوقف الضغط
    stop_event.set()
    time.sleep(1)
    print("\n  Upload flood stopped.")

    return results


def compare_results(without: dict, with_np: dict):
    """يقارن النتائج ويعطي تقييم نهائي."""
    print_header("FINAL COMPARISON")

    print(f"  {'Target':<14} {'Metric':<10} {'No NetPilot':>12} {'NetPilot ON':>12} {'Diff':>10}")
    print(f"  {'-'*14} {'-'*10} {'-'*12} {'-'*12} {'-'*10}")

    improvements = []

    for name in TARGETS:
        key_b = f"{name}_baseline"
        key_l = f"{name}_loaded"

        # Loaded comparison (the important one)
        t_without = without.get(key_l, [])
        t_with = with_np.get(key_l, [])

        if t_without and t_with:
            avg_wo = statistics.mean(t_without)
            avg_w = statistics.mean(t_with)
            diff = avg_w - avg_wo
            pct = ((avg_w - avg_wo) / avg_wo) * 100

            j_wo = calc_jitter(t_without)
            j_w = calc_jitter(t_with)
            j_diff = j_w - j_wo

            print(f"  {name:<14} {'Avg RTT':<10} {avg_wo:>9.0f} ms {avg_w:>9.0f} ms {diff:>+8.0f} ms")
            print(f"  {'':<14} {'Jitter':<10} {j_wo:>9.1f} ms {j_w:>9.1f} ms {j_diff:>+8.1f} ms")
            print(f"  {'':<14} {'P95':<10} {calc_percentile(t_without,95):>9.0f} ms {calc_percentile(t_with,95):>9.0f} ms")
            print(f"  {'':<14} {'Max':<10} {max(t_without):>9.0f} ms {max(t_with):>9.0f} ms")
            print()

            improvements.append(pct)

    # ── Final Verdict ──
    print_header("VERDICT")

    if improvements:
        avg_imp = statistics.mean(improvements)
        if avg_imp < -10:
            print("  NetPilot is IMPROVING latency under load.")
            print(f"  Average improvement: {abs(avg_imp):.0f}%")
        elif avg_imp > 10:
            print("  WARNING: NetPilot is INCREASING latency under load.")
            print(f"  Average degradation: {avg_imp:.0f}%")
            print("  Check bandwidth setting - might be too low.")
        else:
            print("  NetPilot has MINIMAL EFFECT on latency.")
            print("  Possible reasons:")
            print("    - Upload link not saturated enough")
            print("    - ISP already has good queue management")
            print("    - Bandwidth setting needs tuning")


def main():
    print()
    print("*" * 60)
    print("  NetPilot Benchmark v1.0")
    print("  Comprehensive QoS Assessment")
    print("*" * 60)

    if len(sys.argv) > 1 and sys.argv[1] == "--with":
        label = "NetPilot ON"
    elif len(sys.argv) > 1 and sys.argv[1] == "--without":
        label = "No NetPilot"
    else:
        label = "Test"

    results = run_benchmark(label)

    # حفظ النتائج
    import json
    filename = f"benchmark_{'with' if 'ON' in label else 'without'}.json"
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", filename)

    save_data = {}
    for key, times in results.items():
        if times:
            save_data[key] = {
                "times": times,
                "avg": statistics.mean(times),
                "min": min(times),
                "max": max(times),
                "jitter": calc_jitter(times),
                "p95": calc_percentile(times, 95),
                "loss_pct": (1 - len(times) / PING_COUNT) * 100,
            }

    with open(filepath, "w") as f:
        json.dump(save_data, f, indent=2)
    print(f"\n  Results saved to {filename}")


if __name__ == "__main__":
    main()
