"""
CoDel (Controlled Delay) — Active Queue Management.

CoDel بمفاهيم CCNA:
- تخيل طابور الراوتر (interface queue). لما يمتلئ، الراوتر يسقط
  آخر باكت (tail drop) — وهذا سيء لأنه يسقط الكل بنفس الوقت.
- CoDel أذكى: يراقب كم الباكت قاعد بالطابور (sojourn time).
  لو الباكتات تنتظر أكثر من 5ms باستمرار، يبدأ يسقط بعضها
  بشكل تدريجي. هذا يخلّي TCP يقلل سرعته بلطف بدل الانهيار.

المتغيرات الأساسية:
- target: 5ms — الحد المقبول للانتظار بالطابور
- interval: 100ms — فترة المراقبة
- dropping: هل نحن بوضع الإسقاط ولا لا
"""

import time
import threading
from collections import deque
from dataclasses import dataclass, field


@dataclass
class QueuedPacket:
    """باكت بالطابور مع وقت الدخول."""
    packet: object          # pydivert packet
    enqueue_time: float     # متى دخل الطابور (monotonic)
    flow_id: str            # معرّف الـ flow
    tin: int                # رقم الـ tin (0=voice, 1=high, 2=normal, 3=bulk)
    size: int               # حجم الباكت بالبايت


class CoDel:
    """
    CoDel AQM — يقرر هل نسقط الباكت ولا نمرّره.

    الخوارزمية:
    1. لما باكت يطلع من الطابور، نحسب sojourn_time (كم انتظر)
    2. لو sojourn_time < target (5ms): كل شي تمام، نمرّره
    3. لو sojourn_time > target لمدة أطول من interval (100ms):
       ندخل وضع الإسقاط — نسقط باكتات بتسارع
    4. لما الطابور يفضى أو sojourn_time ينزل: نوقف الإسقاط

    ملاحظة: CoDel ما يحتاج lock خاص لأنه يُستدعى فقط من
    FairQueue.dequeue() الي أصلاً ماسك lock. (thread-safe by caller)
    """

    # ثوابت CoDel — محسوبة مرة وحدة
    TARGET_MS = 5.0              # الحد المقبول للانتظار
    INTERVAL_SEC = 0.1           # فترة المراقبة (100ms بالثواني — محسوبة مسبقاً)

    def __init__(self):
        self._dropping = False
        self._first_above_time = 0.0
        self._drop_next = 0.0
        self._drop_count = 0

        # إحصائيات
        self.total_packets = 0
        self.dropped_packets = 0

    def should_drop(self, sojourn_ms: float, now: float) -> bool:
        """
        يقرر هل نسقط هالباكت ولا لا.
        يُستدعى فقط من FairQueue.dequeue() تحت lock — ما يحتاج lock ثاني.
        """
        self.total_packets += 1

        if sojourn_ms < self.TARGET_MS:
            self._first_above_time = 0.0
            self._dropping = False
            return False

        # sojourn > target
        if self._first_above_time == 0.0:
            self._first_above_time = now
            return False

        if not self._dropping:
            if now - self._first_above_time >= self.INTERVAL_SEC:
                self._dropping = True
                self._drop_count = 1
                self._drop_next = now
                self.dropped_packets += 1
                return True
            return False

        # وضع الإسقاط — نسقط بتسارع
        if now >= self._drop_next:
            self._drop_count += 1
            self._drop_next = now + self.INTERVAL_SEC / (self._drop_count ** 0.5)
            self.dropped_packets += 1
            return True

        return False

    def reset(self):
        """يصفّر الحالة."""
        self._dropping = False
        self._first_above_time = 0.0
        self._drop_next = 0.0
        self._drop_count = 0

    def get_stats(self) -> dict:
        return {
            "total": self.total_packets,
            "dropped": self.dropped_packets,
            "drop_rate_pct": (self.dropped_packets / max(self.total_packets, 1)) * 100,
            "dropping_state": self._dropping,
        }
