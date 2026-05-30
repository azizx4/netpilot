# NetPilot

**Experimental user-space traffic control for Windows, built on WinDivert.**

NetPilot is a proof-of-concept that explores whether user-space packet interception on Windows can reduce latency under network load — without building a custom NDIS kernel driver.

> **Status:** Research prototype. Not production-ready. Results are from a limited test environment and should not be generalized.

---

## What it does

When multiple applications compete for bandwidth, queues build up at the bottleneck (modem/router), causing latency spikes — a phenomenon known as **bufferbloat**. NetPilot intercepts packets and applies queue management to keep latency low.

### Upload (full control)
Outbound packets are intercepted via WinDivert and processed through:
- **4-Tin priority queues** (Voice > High > Normal > Bulk)
- **Fair Queue** — round-robin per flow (like CBWFQ)
- **CoDel AQM** — drops packets based on sojourn time, not queue size
- **Token Bucket** — rate limiting
- **Fast-path bypass** — priority processes skip the entire SQM pipeline

### Download (best-effort mitigation)
Endpoint-side download control is fundamentally limited — bufferbloat occurs at the modem before packets reach the PC. NetPilot mitigates this through:
- **TCP Window Clamping** — modifies outbound ACK window field to signal the server to slow down
- **Rate Policing** — token bucket pass/drop on inbound packets (catches UDP/QUIC)
- **Adaptive RTT feedback** — background ping adjusts window proportionally to measured congestion

> Endpoint-side download control is fundamentally limited. This is documented honestly throughout the project.

---

## Benchmark Results

> Single test environment — Windows 11, ~17 Mbps download, Saudi Arabia. Not generalizable.

### YouTube 4K + 3x 100MB downloads (heavy congestion)

| Condition | Avg | Median | Max | Jitter | Pings <=150ms |
|-----------|-----|--------|-----|--------|---------------|
| **No QoS** | 578ms | 519ms | 1,759ms | 276ms | 16% |
| **NetPilot** | 134ms | 116ms | 319ms | 34ms | 83% |

### YouTube 4K + Upload flood (bidirectional load)

| Condition | Avg | Max | Jitter |
|-----------|-----|-----|--------|
| **No QoS** | 278ms | 996ms | 178ms |
| **NetPilot** | 122ms | 136ms | 8.5ms |

Full results and methodology: [NetPilot_Full_Report.md](NetPilot_Full_Report.md)

---

## Architecture

```
Upload Path:
  Outbound TCP/UDP ──> WinDivert capture
      ├── Priority PID ──> Fast-path deque ──> Send immediately
      ├── Priority IP  ──> Voice tin (strict priority)
      └── Everything else ──> Normal/Bulk tin
          All tins ──> Fair Queue ──> CoDel AQM ──> Token Bucket ──> Send

Download Path:
  Layer 1: TCP Window Clamping (outbound ACKs — proactive)
  Layer 2: Rate Policing (inbound token bucket — reactive)
  RTT Monitor: Background ping ──> proportional window adjustment

ICMP ──> kernel direct (zero overhead from NetPilot)
```

---

## Requirements

- Windows 10/11
- Python 3.11+
- **Administrator privileges** (WinDivert requires elevated access)

## Installation

```bash
git clone https://github.com/azizx4/netpilot.git
cd netpilot
pip install -r requirements.txt
```

## Usage

```bash
# GUI mode (recommended)
python -m netpilot

# CLI mode
python -m netpilot.engine -r 375 -d 1500
#   -r  Upload limit in KB/s
#   -d  Download limit in KB/s (0 = disabled)

# Run benchmark
python -m netpilot.benchmark --without   # baseline
python -m netpilot.benchmark --with      # with NetPilot running
```

---

## Project Structure

```
netpilot/
├── __main__.py          # Entry point
├── engine.py            # Core engine — SQM + Fast-Path + Download control
├── sqm.py               # Smart Queue Management — Fair Queue + CoDel + Token Bucket
├── codel.py             # CoDel AQM algorithm
├── gui.py               # CustomTkinter GUI
├── config.py            # Settings load/save
├── speed_detect.py      # Auto-detect NIC speed
├── process_detector.py  # Maps PIDs to process names
├── packet_monitor.py    # Standalone traffic monitor
├── benchmark.py         # Automated benchmark suite
└── upload_stress.py     # Upload flood for testing
```

---

## Limitations

- **Download control is approximate** — bufferbloat at the modem cannot be fully solved from the endpoint
- **TCP Window Clamping does not affect QUIC/UDP** — an increasing share of web traffic uses QUIC
- **May conflict with VPNs, firewalls, or other packet-intercepting software**
- **Results are environment-specific** — different hardware, ISPs, and link speeds will produce different results
- **Not a replacement for proper SQM/CAKE on a router** — that remains the correct solution for bufferbloat

## License

MIT — see [LICENSE](LICENSE).

This project depends on [WinDivert](https://reqrypt.org/windivert.html) (via [PyDivert](https://github.com/ffalcinelli/pydivert)), which is licensed under LGPL-3.0 / GPL-2.0.
