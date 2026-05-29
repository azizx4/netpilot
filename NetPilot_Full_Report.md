# NetPilot — Technical Report

> **Status: Experimental Proof of Concept.** This project is not production-ready and may affect connectivity on some systems. It is intended for learning, research, and controlled testing.

> **Project name is temporary** and may change before public release.

*Date: 2026-05-29*
*Author: Development Session Log*

---

## 1. What is NetPilot?

An experimental Windows user-space traffic control Proof of Concept for studying and mitigating latency under load.

This project explores whether user-space packet interception on Windows can reduce latency under load without building a custom NDIS kernel driver. It combines WinDivert-based packet interception, process-aware flow classification, Fair Queuing, CoDel-inspired queue management, Token Bucket rate limiting, TCP Window Clamping, inbound policing, and adaptive RTT feedback. The purpose is educational and experimental: to understand how queueing, congestion, and flow-control behavior affect latency and jitter under network load.

---

## 2. Technical Specifications

### Core Engine
| Component | Technology |
|-----------|-----------|
| Platform | Windows 10/11 (requires Administrator) |
| Language | Python 3.11+ |
| Packet interception | WinDivert (via PyDivert) — userspace packet control |
| Upload control | SQM: 4-Tin Fair Queue + CoDel AQM + Token Bucket |
| Download control | TCP Window Clamping (proactive) + Rate Policing (reactive) |
| Priority fast-path | Lock-free deque bypass for priority processes |
| RTT monitoring | Background ping every 1.5s with proportional window adjustment |
| Process detection | psutil — maps ports to PIDs to identify process traffic |
| GUI | CustomTkinter — light theme |
| Configuration | JSON file — apps, IPs, bandwidth settings |

**Note on WinDivert:** NetPilot does not implement a custom NDIS kernel driver, but it relies on WinDivert, which uses a kernel-mode driver for packet interception. This is an important distinction — the project is "userspace" in terms of its own code, but the underlying interception mechanism operates at kernel level via WinDivert's driver.

### Upload Path (SQM Engine)
```
Outbound TCP/UDP --> WinDivert capture
    |
    +--> Priority processes (by PID) --> Fast-path deque --> Send immediately
    |
    +--> IP-based priority --> Voice/High tin (priority queue)
    |
    +--> Everything else --> Normal/Bulk tin
    |
    All tins --> Fair Queue (round-robin per flow)
            --> CoDel AQM (drop if sojourn > 5ms for 100ms)
            --> Token Bucket (rate limit upload)
            --> WinDivert send

ICMP (ping) --> kernel direct (zero overhead from NetPilot)
Inbound traffic --> separate handle (download control)
```

### Download Path (Dual-Layer)
```
Layer 1 — TCP Window Clamping (proactive):
  - Modifies TCP Window field in outbound ACK packets
  - Tells remote server: "don't send more than X bytes"
  - Server naturally slows down via TCP flow control
  - Window size = f(download_rate, measured_RTT, congestion_ratio)
  - Incremental checksum update (RFC 1624)
  - Adaptive: RTT monitor adjusts window proportionally every 1.5s

Layer 2 — Rate Policing (reactive):
  - Token bucket on inbound packets (8KB burst)
  - Pass if tokens available, drop if not
  - TCP detects drops --> reduces sending rate
  - Catches what Window Clamping misses (UDP/QUIC, initial bursts)
  - Priority processes/IPs bypass policing
```

### 4-Tin Queue System
| Tin | Priority | Classification | Queue Size | Rate Control |
|-----|----------|---------------|------------|-------------|
| Voice | Highest (strict) | User-configured "Very High" processes | 50 packets | No token bucket |
| Game | High (strict) | User-configured "High" processes | 100 packets | No token bucket |
| Normal | Medium (weighted 3:1) | Unclassified traffic | 500 packets | Token bucket limited |
| Bulk | Low (weighted 3:1) | Background traffic | 1000 packets | Token bucket limited |

### Optimizations Applied
| Optimization | Effect |
|-------------|--------|
| Outbound-only WinDivert filter | ICMP and download don't touch userspace for upload path |
| Lock-free PID/IP lookups | Atomic dict swap instead of mutex |
| Lock-free CoDel | Called under FairQueue lock, no double-locking |
| Lock-free token bucket | Single-thread access, no synchronization needed |
| Tuple flow IDs | No string allocation per packet |
| Direct queue references | No dict lookup for tin access |
| Fast-path deque | Priority processes bypass entire SQM pipeline |
| 8KB download burst | Smoother policing than 64KB |
| Proportional RTT adaptation | Continuous window adjustment, not step thresholds |
| Process refresh every 2s | Reduces psutil overhead |
| Port refresh every 500ms | Keeps PID mapping current |

---

## 3. Benchmark Results

> **Important:** These results are from a limited local test environment and should not be generalized without broader testing. Network behavior varies significantly across ISPs, hardware, link speeds, and geographic locations.

### Test Environment
- **OS:** Windows 11 Home
- **Location:** Saudi Arabia
- **Download speed:** ~17 Mbps (ISP measured)
- **Link speed:** 433 Mbps (NIC)
- **Baseline ping to Discord:** ~115-120ms
- **Test targets:** Discord (162.159.x.x), Cloudflare (1.1.1.1), Google DNS (8.8.8.8)

---

### Test A: Upload Stress Only

**Condition:** Python UDP+TCP flood to 9.9.9.9

| Metric | No QoS | NetPilot v2 (initial) | NetPilot v4 (optimized) |
|--------|--------|----------------------|------------------------|
| Avg | 127ms | 141ms | **127ms** |
| Max | 141ms | 315ms | **145ms** |
| Jitter | 6.4ms | 21.2ms | **11.2ms** |

**Notes:** The upload flood from Python did not saturate the 433 Mbps link. This test primarily verified that NetPilot does not add measurable overhead. A proper upload bufferbloat test would require saturating the actual ISP upload bandwidth.

---

### Test B: YouTube 4K Streaming (download load)

**Condition:** YouTube 4K video playing

| Metric | No QoS | NetPilot (upload-only) | NetPilot + DL Policing |
|--------|--------|----------------------|----------------------|
| Avg | 336ms | 296ms | **125ms** |
| Max | 617ms | 710ms | **241ms** |
| Jitter | 241ms | 98ms | **21ms** |

**Notes:** Without download control, NetPilot could not address download-side bufferbloat. Adding download policing showed significant improvement. YouTube streams in bursts, causing periodic buffer fills at the modem.

---

### Test C: YouTube 4K + 3x 100MB Downloads (heavy congestion)

**Condition:** YouTube 4K + three simultaneous 100MB file downloads from speedtest.tele2.net

This was the primary stress test — simulating heavy concurrent download usage.

#### Evolution of results:

| Version | Avg | Median | Max | Jitter | <=150ms | >500ms |
|---------|-----|--------|-----|--------|---------|--------|
| **No QoS (combined, 49 pings)** | 578ms | 519ms | 1,759ms | 276ms | 16% | 53% |
| Policing 64KB burst | 226ms | 176ms | 808ms | 140ms | 48% | 8% |
| Policing 8KB burst | 196ms | 156ms | 418ms | 82ms | 40% | 0% |
| Shaping (queue+release) | 425ms | 342ms | 955ms | 262ms | — | 32% |
| Window Clamping only | 889ms | — | 3,394ms | — | — | — |
| WClamp + Policing | 148ms | 129ms | 428ms | 50ms | 76% | 0% |
| WClamp + Policing + Adaptive RTT | 137ms | 128ms | 189ms | 27ms | 82% | 0% |
| **Final version** | **134ms** | **116ms** | **319ms** | **34ms** | **83%** | **0%** |

#### Failed approaches and lessons:

1. **Download Shaping (queue+release) performed worse than Policing (425ms vs 196ms)**
   - Queuing inbound packets in userspace added delay — the queue itself became a latency source
   - For download-side control from the endpoint, dropping early (policing) was more effective than delaying (shaping)

2. **Window Clamping alone was ineffective (889ms avg)**
   - Without policing as a backup, new TCP connections flood the link before clamping takes effect
   - YouTube increasingly uses QUIC (UDP), which TCP Window Clamping cannot influence
   - Fresh TCP connections start with large initial windows

3. **The combination of Policing + Window Clamping + Adaptive RTT was most effective**
   - Policing provides a hard rate limit (TCP + UDP)
   - Window Clamping proactively reduces TCP inbound traffic
   - Adaptive RTT tightens control when congestion is detected

---

### Test D: YouTube 4K + Upload Flood (bidirectional load)

**Condition:** YouTube 4K + Python UDP/TCP flood

| Metric | No QoS | NetPilot (full) |
|--------|--------|----------------|
| Avg | 278ms | **122ms** |
| Max | 996ms | **136ms** |
| Jitter | 178ms | **8.5ms** |

**Notes:** This scenario combined both upload and download load. Both the SQM upload path and download control contributed to the result.

---

## 4. Comparison with cFosSpeed

> **Important:** NetPilot showed promising results in a limited local test environment. A fair comparison with cFosSpeed requires same-machine, same-network, same-workload A/B testing, which was not conducted. The cFosSpeed numbers below are from their published documentation, not from our test environment.

### Architecture Difference

| | cFosSpeed | NetPilot |
|--|-----------|---------|
| Runs at | Kernel level (NDIS filter driver) | Userspace (via WinDivert) |
| Download shaping | True kernel-level queue and release | Policing + Window Clamping |
| Maturity | 15+ years of development | Experimental prototype |
| WinDivert dependency | NetPilot does not implement a custom NDIS kernel driver, but it relies on WinDivert, which uses a kernel-mode driver for packet interception. |

### Available Data Points

| Metric | cFosSpeed (their published data) | NetPilot (our local test) |
|--------|--------------------------------|--------------------------|
| Download ping reduction ratio | 3.2x | 4.3x (578 -> 134ms) |
| Upload ping increase | +10ms | +0ms |
| Download ping increase | +5ms | +19ms |

**These numbers are not directly comparable** — they come from different test environments, different link speeds, different geographic locations, and different workloads. The cFosSpeed data is from their controlled testing; the NetPilot data is from a single test environment in Saudi Arabia.

### cFosSpeed Advantages
- Kernel-level packet processing (lower per-packet overhead)
- True download shaping via kernel queue
- NIC hardware offloading support
- 15+ years of maturity and optimization
- Deep packet inspection capabilities

### NetPilot Characteristics
- Free and open source
- Uses modern AQM algorithms (CoDel, Fair Queue)
- Adaptive RTT-based window control
- No custom kernel driver (relies on WinDivert's driver)
- Experimental — not yet tested across diverse environments

---

## 5. Limitations

### Requires specific environment
- **Administrator privileges** required (WinDivert needs elevated access)
- **Depends on WinDivert** — must be installed and compatible with the OS version
- **May conflict with VPNs, firewalls, or security tools** that also intercept packets

### Download control is approximate
- **Endpoint-side download control is fundamentally limited** — bufferbloat occurs at the modem/ISP before packets reach the PC. Policing drops packets after they've already consumed link bandwidth. Window Clamping signals the server to slow down, but takes several RTTs to converge.
- **TCP Window Clamping does not affect QUIC/UDP** — an increasing share of web traffic (YouTube, Google services) uses QUIC, which has its own flow control not influenced by TCP window fields.
- **Initial connection bursts** — New TCP connections start with large windows. Window Clamping takes effect after the first ACK exchange, not during the SYN handshake.

### Results are environment-specific
- All benchmarks were conducted on a single machine, single ISP, single geographic location
- Results may differ significantly on different hardware, link speeds, or network conditions
- **Not production-ready** — this is a research prototype, not a finished product

### Cannot fix
- High baseline latency (geographic distance to servers)
- ISP-level throttling or traffic management
- WiFi interference or signal quality issues
- Hardware bottlenecks

---

## 6. Future Work: Security Visibility Layer

The next phase may explore lightweight defensive security visibility. Since the engine already classifies traffic by process, flow, protocol, and destination, it could be extended to collect network telemetry such as:

- Process-to-destination mapping (which process connects where)
- Suspicious outbound connection indicators
- High-volume upload behavior detection
- PowerShell/CMD network activity monitoring
- Unknown executable network access logging
- Beacon-like periodic connection detection

The goal is not to decrypt or inspect private content, but to analyze network metadata for defensive visibility and anomaly detection. This would position the project as a dual-purpose tool: network quality research and endpoint security telemetry.

---

## 7. File Structure

```
C:\ccf\
├── CLAUDE.md                    # Project development rules
├── netpilot_config.json         # User settings (bandwidth, apps, IPs)
├── requirements.txt             # Dependencies
├── NetPilot_vs_cFosSpeed.md     # Comparison notes
├── NetPilot_Full_Report.md      # This file
└── netpilot/
    ├── __init__.py
    ├── __main__.py              # Entry point: python -m netpilot
    ├── engine.py                # Core engine — SQM + Fast-Path + DL control
    ├── sqm.py                   # Smart Queue Management — Fair Queue + CoDel
    ├── codel.py                 # CoDel AQM algorithm
    ├── config.py                # Settings load/save (apps, IPs, bandwidth)
    ├── gui.py                   # GUI — CustomTkinter
    ├── speed_detect.py          # Auto-detect upload & download speed
    ├── process_detector.py      # Process detection (maps PIDs to names)
    ├── packet_monitor.py        # Traffic monitoring (standalone)
    ├── upload_stress.py         # Upload stress test tool
    └── benchmark.py             # Automated benchmark suite
```

### Dependencies
```
pydivert>=3.1.3      # WinDivert Python wrapper
psutil>=5.9.0        # Process and network detection
customtkinter>=5.2.0 # GUI toolkit
```

---

## 8. How to Run

```bash
# Install dependencies
pip install pydivert psutil customtkinter

# Run (requires Administrator)
cd C:\ccf
python -m netpilot

# Or run engine only (CLI)
python -m netpilot.engine -r 375 -d 1500

# Run benchmark
python -m netpilot.benchmark --with
python -m netpilot.benchmark --without
```

---

## 9. Raw Test Data

### No QoS — YouTube 4K + 3x100MB (Run 1, 25 pings)
```
132 217 152 110 254 203 428 343 228 325 714 519 413 450 1557 517 871 1221 873 710 177 643 680 779 177
Avg=508ms Max=1557ms Min=110ms
```

### No QoS — YouTube 4K + 3x100MB (Run 2, 24 pings)
```
601 238 136 302 145 1593 995 914 1226 1339 1759 734 581 564 1154 559 545 135 126 139 119 461 608 637
Avg=629ms Max=1759ms Min=119ms
```

### NetPilot Final — YouTube 4K + 3x100MB (24 pings)
```
116 121 135 112 319 112 116 111 111 106 133 126 117 161 144 222 165 109 108 109 114 114 117 122
Avg=134ms Max=319ms Min=106ms Median=116ms
```

### NetPilot — YouTube 4K + Upload Flood (20 pings)
```
136 112 117 128 117 126 112 121 130 124 122 122 124 114 117 129 134 120 115 125
Avg=122ms Max=136ms Min=112ms
```

---

## 10. Conclusion

This project is best understood as a research prototype and engineering experiment. Its value is not in claiming to be a finished network optimizer, but in demonstrating how known networking concepts — Fair Queuing, CoDel, Token Buckets, TCP Window Clamping, and adaptive RTT feedback — can be combined and tested in a Windows user-space environment.

The benchmark results suggest that meaningful latency reduction under load is achievable from userspace, but these results are from a single test environment and require broader validation. The project serves as a foundation for further research into user-space traffic control, bufferbloat mitigation, and potentially endpoint security visibility on Windows.
