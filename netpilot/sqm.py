"""
SQM (Smart Queue Management) — Fair Queue + 4 Tins + CoDel.
Experimental implementation for studying queue management behavior.

بمفاهيم CCNA:
- الـ 4 Tins تشبه class-map + policy-map بالـ MQC:
  * Voice (EF) → أعلى أولوية، مثل priority queue
  * Gaming (AF41) → أولوية عالية
  * Normal (AF21) → best effort
  * Bulk (AF11) → scavenger/bulk

- Fair Queue يشبه CBWFQ — كل flow تاخذ حصة عادلة من الباندوث
- CoDel يشبه WRED بس أذكى — يراقب التأخير مو حجم الطابور

التحسينات عن v2:
- classify() بدون lock — atomic dict swap بدل mutex
- consume_tokens() بدون lock — بس send_loop يستخدمه
- flow_id = tuple بدل f-string — أرخص بكثير
- dequeue() بدون .size property calls — يقلل lock contention
- CoDel بدون lock — يُستدعى تحت FairQueue lock
"""

import time
import threading
from collections import deque, defaultdict
from dataclasses import dataclass

from netpilot.codel import CoDel, QueuedPacket


# ── Tin definitions ──────────────────────────────────
TIN_VOICE  = 0
TIN_GAME   = 1
TIN_NORMAL = 2
TIN_BULK   = 3

TIN_NAMES = {
    TIN_VOICE:  "Voice",
    TIN_GAME:   "Game",
    TIN_NORMAL: "Normal",
    TIN_BULK:   "Bulk",
}

TIN_WEIGHTS = {
    TIN_VOICE:  8,
    TIN_GAME:   6,
    TIN_NORMAL: 3,
    TIN_BULK:   1,
}

# حد أقصى للطابور لكل tin (عدد الباكتات)
TIN_MAX_QUEUE = {
    TIN_VOICE:  50,
    TIN_GAME:   100,
    TIN_NORMAL: 500,
    TIN_BULK:   1000,
}


@dataclass
class TinStats:
    """إحصائيات tin واحد."""
    name: str
    packets_sent: int = 0
    packets_dropped: int = 0
    bytes_sent: int = 0
    current_queue_size: int = 0


class FairQueue:
    """
    Fair Queue لـ tin واحد.
    كل flow لها sub-queue. نخدم الـ flows بالتناوب (round-robin).
    """

    def __init__(self, tin_id: int):
        self.tin_id = tin_id
        self.max_queue = TIN_MAX_QUEUE[tin_id]
        self.codel = CoDel()

        # flow_id → deque of QueuedPackets
        self._flows: dict = defaultdict(deque)
        self._flow_order: list = []
        self._rr_index = 0
        self._total_size = 0
        self._lock = threading.Lock()

        self.stats = TinStats(name=TIN_NAMES[tin_id])

    def enqueue(self, qpkt: QueuedPacket) -> bool:
        """يضيف باكت للطابور. False لو ممتلئ (tail drop)."""
        with self._lock:
            if self._total_size >= self.max_queue:
                self.stats.packets_dropped += 1
                return False

            fid = qpkt.flow_id
            flow_q = self._flows[fid]

            # لو الـ flow جديدة أو كانت فاضية، نضيفها للـ round-robin
            if not flow_q:
                self._flow_order.append(fid)

            flow_q.append(qpkt)
            self._total_size += 1
            self.stats.current_queue_size = self._total_size
            return True

    def dequeue(self) -> QueuedPacket | None:
        """يطلّع باكت بـ round-robin. يطبّق CoDel."""
        with self._lock:
            if self._total_size == 0:
                return None

            attempts = len(self._flow_order)
            for _ in range(attempts):
                if not self._flow_order:
                    break

                self._rr_index = self._rr_index % len(self._flow_order)
                fid = self._flow_order[self._rr_index]

                flow_q = self._flows.get(fid)
                if not flow_q:
                    self._flow_order.pop(self._rr_index)
                    self._flows.pop(fid, None)
                    continue

                qpkt = flow_q.popleft()
                self._total_size -= 1
                self.stats.current_queue_size = self._total_size

                if not flow_q:
                    self._flow_order.pop(self._rr_index)
                    del self._flows[fid]
                else:
                    self._rr_index += 1

                # CoDel check — بدون lock إضافي (إحنا أصلاً ماسكين _lock)
                now = time.monotonic()
                sojourn_ms = (now - qpkt.enqueue_time) * 1000

                if self.codel.should_drop(sojourn_ms, now):
                    self.stats.packets_dropped += 1
                    continue

                self.stats.packets_sent += 1
                self.stats.bytes_sent += qpkt.size
                return qpkt

            return None

    @property
    def is_empty(self) -> bool:
        """أسرع من .size — ما يحتاج lock لقراءة int بـ CPython."""
        return self._total_size == 0


class SQMEngine:
    """
    Smart Queue Management — المحرك الرئيسي.
    v3: أقل locks على الـ hot path.
    """

    def __init__(self, bandwidth_kbps: int = 375):
        self.bandwidth_kbps = bandwidth_kbps
        self.rate_bytes_per_sec = bandwidth_kbps * 1024

        # Token bucket — بدون lock (بس send_loop يستخدمه)
        self._tokens = float(32 * 1024)
        self._max_tokens = float(32 * 1024)
        self._last_refill = time.monotonic()

        # 4 Tins — نحفظ references مباشرة عشان نتجنب dict lookup
        self._voice_q = FairQueue(TIN_VOICE)
        self._game_q = FairQueue(TIN_GAME)
        self._normal_q = FairQueue(TIN_NORMAL)
        self._bulk_q = FairQueue(TIN_BULK)

        # dict للوصول بالـ index (إحصائيات + enqueue)
        self.tins: dict[int, FairQueue] = {
            TIN_VOICE:  self._voice_q,
            TIN_GAME:   self._game_q,
            TIN_NORMAL: self._normal_q,
            TIN_BULK:   self._bulk_q,
        }

        # PID → priority — atomic swap بدل lock
        self._pid_priority: dict[int, str] = {}

        # IP → priority — atomic swap
        self._ip_priority: dict[str, str] = {}

        # Weighted round-robin counter
        self._wr_count = 0

    def set_pid_priorities(self, pid_priority: dict[int, str]):
        """
        يحدّث جدول PID → priority.
        Atomic swap: نبني dict جديد ونحط الـ reference.
        GIL يضمن إن القراءة بـ classify() تشوف إما القديم أو الجديد — مو حالة وسط.
        """
        self._pid_priority = pid_priority.copy()

    def set_ip_priorities(self, ip_priority: dict[str, str]):
        """يحدّث جدول IP → priority. Atomic swap."""
        self._ip_priority = ip_priority.copy()

    def set_bandwidth(self, kbps: int):
        self.bandwidth_kbps = kbps
        self.rate_bytes_per_sec = kbps * 1024

    def classify(self, pid: int | None, is_outbound: bool) -> int:
        """
        يصنّف الباكت — أي tin يروح.

        pid = -1: sentinel من الـ engine → يعني IP-based priority.
        pid > 0: PID عادي → نشيك _pid_priority.
        """
        if pid:
            if pid == -1:
                # IP-based priority — دايم Voice tin
                return TIN_VOICE

            priority = self._pid_priority.get(pid)
            if priority == "very_high":
                return TIN_VOICE
            elif priority == "high":
                return TIN_GAME

        return TIN_NORMAL

    def enqueue(self, packet, flow_id, pid: int | None,
                is_outbound: bool, size: int) -> bool:
        """يصنّف الباكت ويدخّله الطابور المناسب."""
        tin = self.classify(pid, is_outbound)

        qpkt = QueuedPacket(
            packet=packet,
            enqueue_time=time.monotonic(),
            flow_id=flow_id,
            tin=tin,
            size=size,
        )

        return self.tins[tin].enqueue(qpkt)

    def dequeue(self) -> QueuedPacket | None:
        """
        يطلّع الباكت الجاي حسب الأولوية.
        Voice → Game (strict priority)
        Normal → Bulk (weighted 3:1)

        يستخدم direct references بدل dict lookups.
        يستخدم is_empty بدل .size (أرخص).
        """
        # Strict priority: Voice أول، بعدها Game
        pkt = self._voice_q.dequeue()
        if pkt is not None:
            return pkt

        pkt = self._game_q.dequeue()
        if pkt is not None:
            return pkt

        # Weighted: Normal vs Bulk (3:1 ratio)
        normal_empty = self._normal_q.is_empty
        bulk_empty = self._bulk_q.is_empty

        if not normal_empty and not bulk_empty:
            self._wr_count += 1
            if self._wr_count % 4 == 0:
                pkt = self._bulk_q.dequeue()
                if pkt:
                    return pkt
            pkt = self._normal_q.dequeue()
            if pkt:
                return pkt

        if not normal_empty:
            return self._normal_q.dequeue()
        if not bulk_empty:
            return self._bulk_q.dequeue()

        return None

    def consume_tokens(self, nbytes: int) -> float:
        """
        Token bucket — بدون lock.
        بس send_loop يستدعيه (thread واحد) فما نحتاج synchronization.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._max_tokens,
            self._tokens + elapsed * self.rate_bytes_per_sec
        )
        self._last_refill = now

        if self._tokens >= nbytes:
            self._tokens -= nbytes
            return 0.0
        else:
            deficit = nbytes - self._tokens
            self._tokens = 0
            return deficit / self.rate_bytes_per_sec

    def get_stats(self) -> dict:
        result = {}
        total_sent = 0
        total_dropped = 0

        for tin_id, fq in self.tins.items():
            s = fq.stats
            codel_stats = fq.codel.get_stats()
            result[TIN_NAMES[tin_id]] = {
                "sent": s.packets_sent,
                "dropped": s.packets_dropped + codel_stats["dropped"],
                "bytes_sent": s.bytes_sent,
                "queue_size": s.current_queue_size,
                "codel_dropping": codel_stats["dropping_state"],
            }
            total_sent += s.packets_sent
            total_dropped += s.packets_dropped + codel_stats["dropped"]

        result["total"] = {
            "sent": total_sent,
            "dropped": total_dropped,
            "drop_rate_pct": (total_dropped / max(total_sent + total_dropped, 1)) * 100,
        }
        return result


def make_flow_id(packet) -> tuple:
    """
    يبني flow ID من الباكت.
    v3: tuple بدل f-string — أرخص (ما فيه string allocation) وأسرع بالـ hashing.
    """
    proto = 6 if packet.tcp else 17  # TCP=6, UDP=17 (أرقام بدل strings)
    return (proto, packet.src_addr, packet.src_port, packet.dst_addr, packet.dst_port)
