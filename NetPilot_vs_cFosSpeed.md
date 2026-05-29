# NetPilot vs cFosSpeed — Comparison Notes

> **Status: Experimental Proof of Concept.** NetPilot is a research prototype. This comparison is informational, not a product claim.

> **Important:** NetPilot showed promising results in a limited local test environment. A fair comparison with cFosSpeed requires same-machine, same-network, same-workload A/B testing, which was not conducted. The cFosSpeed numbers below are from their published documentation, not from our test environment.

---

## Architecture

| | **cFosSpeed** | **NetPilot** |
|--|-------------|-------------|
| Level | NDIS Kernel Driver | Userspace (via WinDivert) |
| Packet path | Kernel only (no context switch) | Kernel -> User -> Kernel |
| Download control | True shaping (kernel-level queue) | TCP Window Clamping + Policing |
| Upload control | Priority queuing | SQM: Fair Queue + CoDel + Fast-Path |
| Development | 15+ years | Experimental prototype |
| Price | Paid (~$16) | Free & open source |
| Source code | Closed | Open |
| Driver | Custom NDIS filter driver | Relies on WinDivert's kernel driver |

---

## Available Data Points

> These numbers are **not directly comparable**. They come from different test environments, link speeds, locations, and workloads.

### Download Stress

| Metric | **No QoS** | **cFosSpeed** (published) | **NetPilot** (local test) |
|--------|-----------|--------------------------|--------------------------|
| Ping reduction ratio | — | 3.2x | 4.3x (578 -> 134ms) |
| Ping increase during DL | — | +5ms over baseline | +19ms over baseline |

### Upload Stress

| Metric | **No QoS** | **cFosSpeed** (published) | **NetPilot** (local test) |
|--------|-----------|--------------------------|--------------------------|
| Ping reduction ratio | — | 9.8x | Not conclusively tested (link not saturated) |
| Ping increase during UL | — | +10ms | +0ms |

---

## cFosSpeed Advantages

| Advantage | Detail |
|-----------|--------|
| Kernel-level processing | No userspace context switching per packet |
| True download shaping | Queues and releases packets in kernel at controlled rate |
| Hardware offloading | NDIS Task Offload — uses NIC acceleration |
| Maturity | 15+ years of development and testing |
| Auto calibration | Measures link speed precisely over time |
| Deep packet inspection | Per-protocol classification |

## NetPilot Characteristics

| Characteristic | Detail |
|----------------|--------|
| Free and open source | Code is readable and modifiable |
| Modern AQM algorithms | CoDel + Fair Queue (academic research-based) |
| Adaptive RTT feedback | Proportional window adjustment based on measured latency |
| No custom kernel driver | Relies on WinDivert's driver, does not install its own NDIS filter |
| Experimental status | Not production-ready, limited testing |
| IP-based rules | Can prioritize by destination IP, not just application |

---

## Technical Approach Comparison

### cFosSpeed
```
NDIS Filter Driver (kernel level)
    |
    v
Packet Classification (DPI)
    |
    v
Priority Queues (kernel)
    |
    v
Rate-controlled release
    |
    v
Network Adapter (with hardware offload)
```

### NetPilot
```
Upload: WinDivert (outbound) --> Classify --> Fair Queue + CoDel --> Token Bucket --> Send
Download: TCP Window Clamping (outbound ACKs) + Inbound Policing (token bucket pass/drop)
RTT Monitor: Background ping --> Proportional window adjustment
```

---

## Key Difference

The fundamental architectural difference is that cFosSpeed operates as a kernel-level NDIS filter driver with direct access to the packet path, while NetPilot operates in userspace and relies on WinDivert for packet interception. This means:

- cFosSpeed has inherently lower per-packet overhead
- cFosSpeed can perform true download shaping (queue in kernel)
- NetPilot must use indirect methods for download control (window clamping, policing)
- NetPilot compensates with modern AQM algorithms and adaptive feedback

This architectural gap cannot be closed without implementing a custom kernel driver, which is outside NetPilot's current scope.

---

*Test environment: Windows 11, ~17 Mbps download, Saudi Arabia, 2026-05-29*
*cFosSpeed data source: [cFosSpeed Traffic Shaping Report](https://www.cfos.de/files/cfosspeed-traffic-shaping-report.pdf)*
