import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

"""
اكتشاف البرامج المستهدفة (برامج ذات أولوية).

الفكرة:
- نمسح كل العمليات الشغالة عن طريق psutil
- نقارن اسم العملية بقائمة معروفة
- نرجّع قائمة بالبرامج المكتشفة مع PID + نوعها (realtime / voice)
"""

import psutil
from dataclasses import dataclass

# ---------- البرامج المعروفة ----------
# كل برنامج له: اسم الملف (lowercase) ← (اسم العرض، النوع)
# النوع: "realtime" = تطبيق حساس للتأخير، "voice" = مكالمة صوتية
# سهل تضيف عليها بعدين

KNOWN_APPS: dict[str, tuple[str, str]] = {
    # ---- صوت/مكالمات ----
    "discord.exe":          ("Discord",             "voice"),
    "teams.exe":            ("Microsoft Teams",     "voice"),
    "ms-teams.exe":         ("Microsoft Teams",     "voice"),
    "zoom.exe":             ("Zoom",                "voice"),
}


@dataclass
class DetectedApp:
    """برنامج مكتشف شغال الحين."""
    pid: int
    exe_name: str       # مثل "discord.exe"
    display_name: str   # مثل "Discord"
    app_type: str       # "realtime" أو "voice"


def detect_running_apps() -> list[DetectedApp]:
    """
    يمسح كل العمليات ويرجّع اللي نعرفها.

    كيف يشتغل (بمفاهيم CCNA):
    - مثل ARP table بس للبرامج — نبني جدول بالبرامج الحية
      اللي نحتاج نعطيها أولوية.
    - الـ PID يشبه MAC address — معرّف فريد للعملية
      نستخدمه بعدين لربط الباكتات.
    """
    found: list[DetectedApp] = []

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"]
            if name is None:
                continue
            name_lower = name.lower()

            if name_lower in KNOWN_APPS:
                display_name, app_type = KNOWN_APPS[name_lower]
                found.append(DetectedApp(
                    pid=proc.info["pid"],
                    exe_name=name_lower,
                    display_name=display_name,
                    app_type=app_type,
                ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # العملية ماتت أو ما عندنا صلاحية — نتجاوزها
            continue

    return found


def get_priority_pids() -> dict[int, DetectedApp]:
    """
    يرجّع dict من PID → DetectedApp لكل البرامج ذات الأولوية.
    نستخدمه بعدين لما نراقب الباكتات — نشيك هل الباكت
    من PID ذو أولوية ولا لا.
    """
    apps = detect_running_apps()
    return {app.pid: app for app in apps}


# ---------- تشغيل مباشر للاختبار ----------
if __name__ == "__main__":
    print("=== NetPilot — اكتشاف البرامج ===\n")
    apps = detect_running_apps()

    if not apps:
        print("ما لقيت أي برنامج معروف شغال.")
        print("شغّل برنامج ذو أولوية وجرب مرة ثانية.")
    else:
        realtime = [a for a in apps if a.app_type == "realtime"]
        voice = [a for a in apps if a.app_type == "voice"]

        if realtime:
            print(f"تطبيقات حساسة ({len(realtime)}):")
            for app in realtime:
                print(f"  [{app.pid}] {app.display_name}")

        if voice:
            print(f"\nصوت/مكالمات ({len(voice)}):")
            for app in voice:
                print(f"  [{app.pid}] {app.display_name}")

    print("\n=== تم ===")
