"""
Upload stress test — max pressure version.
Combines UDP flood + TCP uploads + curl uploads to saturate the upload link.
"""

import socket
import threading
import time
import sys
import subprocess


def udp_flood(duration: int):
    """UDP flood — fast small packets."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data = b"\x00" * (64 * 1024)
    start = time.time()
    sent = 0
    try:
        while time.time() - start < duration:
            try:
                sock.sendto(data, ("8.8.8.8", 9999))
                sent += len(data)
            except OSError:
                pass
    finally:
        sock.close()
        print(f"[UDP] {sent/(1024*1024):.0f} MB sent")


def tcp_flood(duration: int, thread_id: int):
    """TCP upload — establishes real connections and pushes data."""
    data = b"\x00" * (128 * 1024)
    start = time.time()
    sent = 0
    while time.time() - start < duration:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect(("8.8.8.8", 80))
            while time.time() - start < duration:
                try:
                    n = sock.send(data)
                    sent += n
                except OSError:
                    break
            sock.close()
        except OSError:
            time.sleep(0.1)
    print(f"[TCP-{thread_id}] {sent/(1024*1024):.0f} MB sent")


def curl_upload(duration: int, idx: int):
    """curl upload to speed test server."""
    try:
        subprocess.run(
            ["curl", "-s", "-o", "NUL", "-X", "POST",
             "--max-time", str(duration),
             "--data-binary", "@NUL",
             "--limit-rate", "0",
             "-H", "Content-Type: application/octet-stream",
             "http://httpbin.org/post"],
            timeout=duration + 5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    print(f"[CURL-{idx}] done")


def max_stress(duration: int = 25):
    """Launch everything to max out upload."""
    print(f"MAX upload stress for {duration}s...")
    threads = []

    # 4 UDP floods
    for _ in range(4):
        t = threading.Thread(target=udp_flood, args=(duration,))
        t.start()
        threads.append(t)

    # 4 TCP floods
    for i in range(4):
        t = threading.Thread(target=tcp_flood, args=(duration, i))
        t.start()
        threads.append(t)

    # 2 curl uploads
    for i in range(2):
        t = threading.Thread(target=curl_upload, args=(duration, i))
        t.start()
        threads.append(t)

    print(f"10 threads running (4 UDP + 4 TCP + 2 curl)")

    for t in threads:
        t.join()
    print("All done.")


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    max_stress(duration)
