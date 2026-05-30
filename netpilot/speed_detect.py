import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

"""
Auto-detect upload speed.

نستخدم طريقتين:
1. نقرأ سرعة كرت الشبكة من ويندوز (link speed) — هذي السرعة القصوى النظرية
2. المستخدم يقدر يعدّل يدوي لو سرعة الـ ISP أقل

بمفاهيم CCNA:
- Link speed = سرعة الـ interface (مثل show interface)
- ISP speed = الـ bandwidth الفعلي (عادة أقل من link speed)
- نحتاج نعرف ISP upload speed عشان نحدد الـ SQM صح
"""

import subprocess
import re


def get_link_speed_mbps() -> int | None:
    """
    يقرأ سرعة كرت الشبكة النشط من ويندوز.
    يرجّع السرعة بـ Mbps، أو None لو ما قدر.
    """
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-NetAdapter | Where-Object Status -eq 'Up' | "
             "Sort-Object LinkSpeed -Descending | "
             "Select-Object -First 1 -ExpandProperty LinkSpeed"],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout.strip()
        if not output:
            return None

        # Parse "325 Mbps" or "1 Gbps"
        match = re.match(r"([\d.]+)\s*(Gbps|Mbps|Kbps)", output, re.IGNORECASE)
        if not match:
            return None

        value = float(match.group(1))
        unit = match.group(2).lower()

        if unit == "gbps":
            return int(value * 1000)
        elif unit == "mbps":
            return int(value)
        elif unit == "kbps":
            return max(1, int(value / 1000))

        return None
    except Exception:
        return None


def get_recommended_bandwidth(ratio: float = 0.90) -> tuple[int, int, str]:
    """
    يرجّع (link_speed_kbps, recommended_kbps, adapter_info).

    link_speed = سرعة الكرت (ممكن أعلى من سرعة ISP)
    recommended = link_speed × ratio
    adapter_info = نص وصفي

    ملاحظة: link speed ممكن تكون أعلى من سرعة الـ ISP.
    المستخدم يقدر يعدّل بالسلايدر.
    """
    link_mbps = get_link_speed_mbps()

    if link_mbps is None:
        # ما قدرنا نقرأ — نرجّع قيمة افتراضية
        return 375, 375, "Could not detect — using default"

    link_kbps = int(link_mbps * 1_000_000 / 8 / 1024)  # Mbps → KB/s
    recommended = int(link_kbps * ratio)

    info = f"Link: {link_mbps} Mbps — 90% = {link_mbps * 0.9:.0f} Mbps"
    return link_kbps, recommended, info


def measure_download_speed() -> tuple[int, str]:
    """
    يقيس سرعة الداونلود الفعلية بتحميل ملف صغير.
    يرجّع (speed_kbps, info_text).

    بمفاهيم CCNA:
    - هذا مثل throughput test — نقيس السرعة الفعلية مو النظرية
    - نحمّل ملف من سيرفر سريع ونحسب: حجم ÷ وقت = سرعة
    """
    import urllib.request
    import time

    # نستخدم ملف 1MB من Cloudflare — سريع وموثوق
    test_urls = [
        ("https://speed.cloudflare.com/__down?bytes=2000000", 2_000_000),
        ("https://speedtest.tele2.net/1MB.zip", 1_000_000),
    ]

    for url, expected_size in test_urls:
        try:
            start = time.monotonic()
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "NetPilot/1.0")

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()

            elapsed = time.monotonic() - start
            if elapsed < 0.1:
                elapsed = 0.1

            actual_size = len(data)
            speed_bytes = actual_size / elapsed
            speed_kbps = int(speed_bytes / 1024)
            speed_mbps = speed_bytes * 8 / 1_000_000

            info = f"Download: {speed_mbps:.0f} Mbps (tested {actual_size/1_000_000:.1f} MB in {elapsed:.1f}s)"
            return speed_kbps, info

        except Exception:
            continue

    return 0, "Could not measure download speed"


def get_recommended_download(ratio: float = 0.85) -> tuple[int, int, str]:
    """
    يقيس ويرجّع سرعة داونلود موصى فيها.
    ratio = 0.85 (85% من السرعة الفعلية).
    """
    speed_kbps, info = measure_download_speed()
    if speed_kbps == 0:
        return 0, 0, info

    recommended = int(speed_kbps * ratio)
    return speed_kbps, recommended, info


if __name__ == "__main__":
    link_mbps = get_link_speed_mbps()
    if link_mbps:
        print(f"Detected link speed: {link_mbps} Mbps")
        link_kbps = int(link_mbps * 1_000_000 / 8 / 1024)
        rec = int(link_kbps * 0.90)
        print(f"  As KB/s: {link_kbps:,} KB/s")
        print(f"  90% limit: {rec:,} KB/s ({link_mbps * 0.9:.0f} Mbps)")
        print(f"\n  Note: This is your NIC link speed.")
        print(f"  If your ISP upload is slower, adjust manually.")
    else:
        print("Could not detect link speed.")
