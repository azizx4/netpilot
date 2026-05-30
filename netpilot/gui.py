import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

"""
NetPilot GUI — Experimental traffic control interface.
"""

import threading
import customtkinter as ctk

from netpilot.engine import NetPilotEngine
from netpilot.config import (
    load_config, save_config, add_app, remove_app, add_ip, remove_ip,
    get_priority_apps, PRIORITY_VERY_HIGH, PRIORITY_HIGH,
    DEFAULT_BANDWIDTH, is_ip,
)
from netpilot.speed_detect import get_recommended_bandwidth, get_recommended_download


# ── Theme ──────────────────
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# Colors
BG           = "#f0f0f0"
CARD_BG      = "#ffffff"
SIDEBAR_BG   = "#2d2d2d"
SIDEBAR_TEXT = "#cccccc"
SIDEBAR_SEL  = "#ff6633"  # Burp orange
ACCENT       = "#ff6633"
ACCENT_HOVER = "#e5552a"
TEXT         = "#1a1a1a"
TEXT_DIM     = "#777777"
BORDER       = "#d4d4d4"
GREEN        = "#22c55e"
RED          = "#ef4444"
VERY_HIGH_C  = "#ff6633"
HIGH_C       = "#3b82f6"
NORMAL_C     = "#94a3b8"

TAB_NAMES = ["Dashboard", "Priority Rules", "Settings"]


class NetPilotGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("NetPilot")
        self.geometry("700x550")
        self.minsize(650, 500)
        self.configure(fg_color=BG)

        # State
        self.engine_running = False
        self._engine: NetPilotEngine | None = None
        self._config = load_config()

        # Layout: sidebar + content
        self._build_sidebar()
        self._content_frame = ctk.CTkFrame(self, fg_color=BG)
        self._content_frame.pack(side="right", fill="both", expand=True)

        # Tab frames
        self._tabs: dict[str, ctk.CTkFrame] = {}
        self._build_dashboard_tab()
        self._build_rules_tab()
        self._build_settings_tab()

        # Show dashboard
        self._select_tab("Dashboard")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ══════════════════════════════════════════════
    #  SIDEBAR
    # ══════════════════════════════════════════════

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, fg_color=SIDEBAR_BG, width=160, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Logo
        ctk.CTkLabel(
            sb, text="NetPilot",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=ACCENT,
        ).pack(pady=(20, 2))

        ctk.CTkLabel(
            sb, text="Traffic Control PoC",
            font=ctk.CTkFont(size=10),
            text_color=SIDEBAR_TEXT,
        ).pack(pady=(0, 20))

        # Tab buttons
        self._tab_btns: dict[str, ctk.CTkButton] = {}
        for name in TAB_NAMES:
            btn = ctk.CTkButton(
                sb, text=name,
                font=ctk.CTkFont(size=13),
                fg_color="transparent",
                hover_color="#404040",
                text_color=SIDEBAR_TEXT,
                anchor="w",
                height=36,
                corner_radius=0,
                command=lambda n=name: self._select_tab(n),
            )
            btn.pack(fill="x", padx=0)
            self._tab_btns[name] = btn

        # Power button at bottom
        spacer = ctk.CTkFrame(sb, fg_color="transparent")
        spacer.pack(fill="both", expand=True)

        self.power_btn = ctk.CTkButton(
            sb, text="START",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=GREEN, hover_color="#1aab50",
            text_color="#ffffff",
            height=40, corner_radius=8,
            command=self._toggle_engine,
        )
        self.power_btn.pack(fill="x", padx=12, pady=(0, 8))

        self.status_label = ctk.CTkLabel(
            sb, text="Idle",
            font=ctk.CTkFont(size=10),
            text_color=SIDEBAR_TEXT,
        )
        self.status_label.pack(pady=(0, 16))

    def _select_tab(self, name: str):
        # Hide all
        for tab in self._tabs.values():
            tab.pack_forget()
        # Highlight button
        for btn_name, btn in self._tab_btns.items():
            if btn_name == name:
                btn.configure(fg_color=SIDEBAR_SEL, text_color="#ffffff")
            else:
                btn.configure(fg_color="transparent", text_color=SIDEBAR_TEXT)
        # Show selected
        self._tabs[name].pack(fill="both", expand=True)

    # ══════════════════════════════════════════════
    #  TAB 1: DASHBOARD
    # ══════════════════════════════════════════════

    def _build_dashboard_tab(self):
        tab = ctk.CTkFrame(self._content_frame, fg_color=BG)
        self._tabs["Dashboard"] = tab

        # Header
        ctk.CTkLabel(
            tab, text="Dashboard",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=24, pady=(20, 16))

        # Tins grid
        tins_frame = ctk.CTkFrame(tab, fg_color="transparent")
        tins_frame.pack(fill="x", padx=24)
        tins_frame.columnconfigure((0, 1, 2, 3), weight=1)

        self.tin_labels: dict[str, dict] = {}
        tins = [
            ("Very High", 0, VERY_HIGH_C),
            ("High", 1, HIGH_C),
            ("Normal", 2, NORMAL_C),
            ("Bulk", 3, TEXT_DIM),
        ]
        tin_map = {"Very High": "Voice", "High": "High", "Normal": "Normal", "Bulk": "Bulk"}

        for display_name, col, color in tins:
            card = ctk.CTkFrame(tins_frame, fg_color=CARD_BG, corner_radius=10,
                                border_width=1, border_color=BORDER)
            card.grid(row=0, column=col, padx=4, pady=4, sticky="nsew")

            ctk.CTkLabel(
                card, text=display_name,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=color,
            ).pack(pady=(12, 2))

            sent_label = ctk.CTkLabel(
                card, text="0",
                font=ctk.CTkFont(size=22, weight="bold"),
                text_color=TEXT,
            )
            sent_label.pack()

            ctk.CTkLabel(
                card, text="packets",
                font=ctk.CTkFont(size=10), text_color=TEXT_DIM,
            ).pack()

            info_label = ctk.CTkLabel(
                card, text="0 KB",
                font=ctk.CTkFont(size=10), text_color=TEXT_DIM,
            )
            info_label.pack(pady=(0, 12))

            self.tin_labels[tin_map[display_name]] = {
                "sent": sent_label,
                "info": info_label,
            }

        # Totals row
        totals_frame = ctk.CTkFrame(tab, fg_color="transparent")
        totals_frame.pack(fill="x", padx=24, pady=(12, 0))
        totals_frame.columnconfigure((0, 1, 2, 3), weight=1)

        self.total_sent_label = self._make_total_box(totals_frame, "Sent", "0", GREEN, 0)
        self.total_dropped_label = self._make_total_box(totals_frame, "Dropped", "0", RED, 1)
        self.drop_rate_label = self._make_total_box(totals_frame, "Drop Rate", "0%", ACCENT, 2)
        self.captured_label = self._make_total_box(totals_frame, "Captured", "0", TEXT_DIM, 3)

        # Active apps
        active_card = ctk.CTkFrame(tab, fg_color=CARD_BG, corner_radius=10,
                                    border_width=1, border_color=BORDER)
        active_card.pack(fill="x", padx=24, pady=(16, 0))

        ctk.CTkLabel(
            active_card, text="Active Priority Apps",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=16, pady=(12, 4))

        self.active_apps_label = ctk.CTkLabel(
            active_card, text="None — add rules in Priority Rules tab",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_DIM,
        )
        self.active_apps_label.pack(anchor="w", padx=16, pady=(0, 12))

    def _make_total_box(self, parent, label, value, color, col):
        card = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=10,
                            border_width=1, border_color=BORDER)
        card.grid(row=0, column=col, padx=4, pady=4, sticky="nsew")

        val = ctk.CTkLabel(
            card, text=value,
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=color,
        )
        val.pack(pady=(10, 0))

        ctk.CTkLabel(
            card, text=label,
            font=ctk.CTkFont(size=10), text_color=TEXT_DIM,
        ).pack(pady=(0, 10))

        return val

    # ══════════════════════════════════════════════
    #  TAB 2: PRIORITY RULES
    # ══════════════════════════════════════════════

    def _get_running_apps(self) -> list[str]:
        """يرجّع قائمة كل البرامج الشغالة (بدون تكرار، مرتّبة)."""
        import psutil
        names = set()
        skip = {
            "system", "registry", "idle", "svchost.exe", "csrss.exe",
            "smss.exe", "wininit.exe", "services.exe", "lsass.exe",
            "conhost.exe", "dwm.exe", "fontdrvhost.exe", "winlogon.exe",
            "sihost.exe", "taskhostw.exe", "ctfmon.exe", "dllhost.exe",
            "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
            "shellexperiencehost.exe", "textinputhost.exe", "widgetservice.exe",
            "backgroundtaskhost.exe", "audiodg.exe", "spoolsv.exe",
        }
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info["name"]
                if name and name.lower() not in skip:
                    names.add(name.lower())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return sorted(names)

    def _build_rules_tab(self):
        tab = ctk.CTkFrame(self._content_frame, fg_color=BG)
        self._tabs["Priority Rules"] = tab

        ctk.CTkLabel(
            tab, text="Priority Rules",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            tab, text="Add an app or IP address and assign its priority. Everything else is Normal.",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_DIM,
        ).pack(anchor="w", padx=24, pady=(0, 16))

        # Add app row
        add_frame = ctk.CTkFrame(tab, fg_color=CARD_BG, corner_radius=10,
                                  border_width=1, border_color=BORDER)
        add_frame.pack(fill="x", padx=24, pady=(0, 8))

        inner = ctk.CTkFrame(add_frame, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        # Row 1: Search + Priority + Add
        ctk.CTkLabel(
            inner, text="App:",
            font=ctk.CTkFont(size=12),
            text_color=TEXT,
        ).pack(side="left")

        self._app_list = self._get_running_apps()
        self.app_search = ctk.CTkEntry(
            inner, width=200, height=32,
            placeholder_text="App name or IP address...",
            border_color=BORDER, fg_color=BG,
        )
        self.app_search.pack(side="left", padx=(8, 8))
        self.app_search.bind("<KeyRelease>", self._on_search_type)

        ctk.CTkButton(
            inner, text="Refresh",
            font=ctk.CTkFont(size=11),
            fg_color=BORDER, hover_color="#bbb",
            text_color=TEXT,
            width=60, height=28, corner_radius=4,
            command=self._refresh_app_list,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            inner, text="Priority:",
            font=ctk.CTkFont(size=12),
            text_color=TEXT,
        ).pack(side="left")

        self.priority_menu = ctk.CTkOptionMenu(
            inner,
            values=["Very High", "High"],
            width=110, height=32,
            fg_color=ACCENT,
            button_color=ACCENT_HOVER,
            dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT,
        )
        self.priority_menu.set("Very High")
        self.priority_menu.pack(side="left", padx=(8, 12))

        ctk.CTkButton(
            inner, text="Add",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            width=60, height=32, corner_radius=6,
            command=self._add_rule,
        ).pack(side="left")

        # Row 2: Search results list
        self.search_results_frame = ctk.CTkScrollableFrame(
            add_frame, fg_color=BG, height=120,
        )
        self.search_results_frame.pack(fill="x", padx=16, pady=(0, 12))
        self._show_app_results(self._app_list)

        # Rules list
        list_card = ctk.CTkFrame(tab, fg_color=CARD_BG, corner_radius=10,
                                  border_width=1, border_color=BORDER)
        list_card.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        # Header
        header = ctk.CTkFrame(list_card, fg_color=BG, corner_radius=0)
        header.pack(fill="x", padx=1, pady=(1, 0))

        ctk.CTkLabel(
            header, text="  App",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, width=200,
        ).pack(side="left", padx=(12, 0))

        ctk.CTkLabel(
            header, text="Priority",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, width=100,
        ).pack(side="left")

        ctk.CTkLabel(
            header, text="Action",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, width=80,
        ).pack(side="right", padx=(0, 16))

        self.rules_frame = ctk.CTkScrollableFrame(
            list_card, fg_color=CARD_BG, height=250,
        )
        self.rules_frame.pack(fill="both", expand=True, padx=1, pady=(0, 1))

        self._refresh_rules_list()

    def _refresh_app_list(self):
        """يحدّث قائمة البرامج الشغالة."""
        self._app_list = self._get_running_apps()
        self._on_search_type()

    def _on_search_type(self, event=None):
        """يفلتر القائمة حسب ما يكتب المستخدم."""
        query = self.app_search.get().strip().lower()
        if not query:
            self._show_app_results(self._app_list)
        else:
            filtered = [a for a in self._app_list if query in a]
            self._show_app_results(filtered)

    def _show_app_results(self, apps: list[str]):
        """يعرض نتائج البحث كأزرار قابلة للنقر."""
        for w in self.search_results_frame.winfo_children():
            w.destroy()

        if not apps:
            ctk.CTkLabel(
                self.search_results_frame, text="No apps found",
                font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
            ).pack(pady=8)
            return

        for app_name in apps[:50]:  # نعرض أول 50
            already = app_name in self._config.get("apps", {})
            btn = ctk.CTkButton(
                self.search_results_frame,
                text=f"  {app_name}" + (" (added)" if already else ""),
                font=ctk.CTkFont(size=11),
                fg_color=CARD_BG if not already else BG,
                hover_color=BORDER,
                text_color=TEXT if not already else TEXT_DIM,
                border_width=1, border_color=BORDER,
                anchor="w", height=28, corner_radius=4,
                command=lambda n=app_name: self._select_app(n),
            )
            btn.pack(fill="x", pady=1)

    def _select_app(self, app_name: str):
        """لما يضغط على برنامج من القائمة."""
        self.app_search.delete(0, "end")
        self.app_search.insert(0, app_name)

    def _add_rule(self):
        name = self.app_search.get().strip()
        if not name:
            return

        priority_text = self.priority_menu.get()
        priority = PRIORITY_VERY_HIGH if priority_text == "Very High" else PRIORITY_HIGH

        # نشيك هل المدخل IP أو اسم برنامج
        if is_ip(name):
            self._config = add_ip(self._config, name, priority)
        else:
            self._config = add_app(self._config, name, priority)

        self.app_search.delete(0, "end")
        self._refresh_rules_list()
        self._on_search_type()

        if self._engine:
            self._engine.update_config(self._config)

    def _remove_rule(self, key: str, rule_type: str = "app"):
        if rule_type == "ip":
            self._config = remove_ip(self._config, key)
        else:
            self._config = remove_app(self._config, key)
        self._refresh_rules_list()

        if self._engine:
            self._engine.update_config(self._config)

    def _refresh_rules_list(self):
        for widget in self.rules_frame.winfo_children():
            widget.destroy()

        apps = self._config.get("apps", {})
        ips = self._config.get("ips", {})

        if not apps and not ips:
            ctk.CTkLabel(
                self.rules_frame,
                text="No rules yet. Add an app or IP above.",
                font=ctk.CTkFont(size=12),
                text_color=TEXT_DIM,
            ).pack(pady=30)
            return

        # عرض البرامج
        for exe_name, priority in apps.items():
            self._make_rule_row(exe_name, priority, "app")

        # عرض الـ IPs
        for ip_addr, priority in ips.items():
            self._make_rule_row(ip_addr, priority, "ip")

    def _make_rule_row(self, name: str, priority: str, rule_type: str):
        row = ctk.CTkFrame(self.rules_frame, fg_color="transparent", height=36)
        row.pack(fill="x", pady=1)

        # أيقونة نوع القاعدة
        type_label = "IP" if rule_type == "ip" else "APP"
        type_color = "#8b5cf6" if rule_type == "ip" else TEXT_DIM

        ctk.CTkLabel(
            row, text=type_label,
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=type_color, width=30,
        ).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            row, text=name,
            font=ctk.CTkFont(size=12),
            text_color=TEXT, width=180, anchor="w",
        ).pack(side="left", padx=(4, 0))

        p_color = VERY_HIGH_C if priority == PRIORITY_VERY_HIGH else HIGH_C
        p_text = "Very High" if priority == PRIORITY_VERY_HIGH else "High"

        ctk.CTkLabel(
            row, text=p_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=p_color, width=100,
        ).pack(side="left")

        ctk.CTkButton(
            row, text="Remove",
            font=ctk.CTkFont(size=11),
            fg_color=RED, hover_color="#dc2626",
            text_color="#ffffff",
            width=65, height=26, corner_radius=4,
            command=lambda n=name, t=rule_type: self._remove_rule(n, t),
        ).pack(side="right", padx=(0, 12))

    # ══════════════════════════════════════════════
    #  TAB 3: SETTINGS
    # ══════════════════════════════════════════════

    def _build_settings_tab(self):
        tab = ctk.CTkFrame(self._content_frame, fg_color=BG)
        self._tabs["Settings"] = tab

        ctk.CTkLabel(
            tab, text="Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=24, pady=(20, 16))

        # Bandwidth card
        bw_card = ctk.CTkFrame(tab, fg_color=CARD_BG, corner_radius=10,
                                border_width=1, border_color=BORDER)
        bw_card.pack(fill="x", padx=24, pady=(0, 12))

        header_row = ctk.CTkFrame(bw_card, fg_color="transparent")
        header_row.pack(fill="x", padx=16, pady=(14, 4))

        ctk.CTkLabel(
            header_row, text="Bandwidth Limit",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT,
        ).pack(side="left")

        self.auto_detect_btn = ctk.CTkButton(
            header_row, text="Auto-detect",
            font=ctk.CTkFont(size=11),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            width=90, height=28, corner_radius=6,
            command=self._auto_detect_speed,
        )
        self.auto_detect_btn.pack(side="right")

        self.rate_value_label = ctk.CTkLabel(
            header_row, text=f"{self._config.get('bandwidth_kbps', DEFAULT_BANDWIDTH)} KB/s",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=ACCENT,
        )
        self.rate_value_label.pack(side="right", padx=(0, 12))

        self.throttle_slider = ctk.CTkSlider(
            bw_card,
            from_=10, to=50000, number_of_steps=500,
            command=self._on_slider_change,
            fg_color=BORDER,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
        )
        self.throttle_slider.set(self._config.get("bandwidth_kbps", 123))
        self.throttle_slider.pack(fill="x", padx=16, pady=(4, 4))

        hint_row = ctk.CTkFrame(bw_card, fg_color="transparent")
        hint_row.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(hint_row, text="10 KB/s", font=ctk.CTkFont(size=10),
                     text_color=TEXT_DIM).pack(side="left")
        ctk.CTkLabel(hint_row, text="Set to ~90% of your ISP upload speed",
                     font=ctk.CTkFont(size=10), text_color=TEXT_DIM).pack(side="left", padx=8)
        ctk.CTkLabel(hint_row, text="50 MB/s", font=ctk.CTkFont(size=10),
                     text_color=TEXT_DIM).pack(side="right")

        # ── Download Bandwidth Card ──
        dl_card = ctk.CTkFrame(tab, fg_color=CARD_BG, corner_radius=10,
                                border_width=1, border_color=BORDER)
        dl_card.pack(fill="x", padx=24, pady=(0, 12))

        dl_header = ctk.CTkFrame(dl_card, fg_color="transparent")
        dl_header.pack(fill="x", padx=16, pady=(14, 4))

        ctk.CTkLabel(
            dl_header, text="Download Limit",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT,
        ).pack(side="left")

        self.dl_detect_btn = ctk.CTkButton(
            dl_header, text="Auto-detect",
            font=ctk.CTkFont(size=11),
            fg_color="#8b5cf6", hover_color="#7c3aed",
            text_color="#ffffff",
            width=90, height=28, corner_radius=6,
            command=self._auto_detect_download,
        )
        self.dl_detect_btn.pack(side="right")

        self.dl_value_label = ctk.CTkLabel(
            dl_header,
            text=self._format_rate(self._config.get("download_kbps", 0)),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#8b5cf6",
        )
        self.dl_value_label.pack(side="right", padx=(0, 12))

        self.dl_toggle = ctk.CTkSwitch(
            dl_header, text="",
            command=self._on_dl_toggle,
            onvalue=1, offvalue=0,
            progress_color="#8b5cf6",
        )
        dl_enabled = self._config.get("download_kbps", 0) > 0
        if dl_enabled:
            self.dl_toggle.select()
        self.dl_toggle.pack(side="right", padx=(0, 8))

        self.dl_slider = ctk.CTkSlider(
            dl_card,
            from_=100, to=200000, number_of_steps=500,
            command=self._on_dl_slider_change,
            fg_color=BORDER,
            progress_color="#8b5cf6",
            button_color="#8b5cf6",
            button_hover_color="#7c3aed",
        )
        dl_val = self._config.get("download_kbps", 0)
        self.dl_slider.set(dl_val if dl_val > 0 else 10000)
        self.dl_slider.pack(fill="x", padx=16, pady=(4, 4))

        dl_hint = ctk.CTkFrame(dl_card, fg_color="transparent")
        dl_hint.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(dl_hint, text="100 KB/s", font=ctk.CTkFont(size=10),
                     text_color=TEXT_DIM).pack(side="left")
        ctk.CTkLabel(dl_hint, text="Set to ~85% of your ISP download speed",
                     font=ctk.CTkFont(size=10), text_color=TEXT_DIM).pack(side="left", padx=8)
        ctk.CTkLabel(dl_hint, text="200 MB/s", font=ctk.CTkFont(size=10),
                     text_color=TEXT_DIM).pack(side="right")


    # ══════════════════════════════════════════════
    #  ENGINE CONTROL
    # ══════════════════════════════════════════════

    def _toggle_engine(self):
        if self.engine_running:
            self._stop_engine()
        else:
            self._start_engine()

    def _start_engine(self):
        try:
            import pydivert
        except ImportError:
            self._set_status("Error: pydivert not installed")
            return

        rate = int(self.throttle_slider.get())
        dl_rate = int(self.dl_slider.get()) if self.dl_toggle.get() else 0
        self._engine = NetPilotEngine(bandwidth_kbps=rate, download_kbps=dl_rate)
        self._engine.update_config(self._config)

        self._set_status("Starting...")
        self.power_btn.configure(state="disabled")

        def _try_start():
            try:
                self._engine.start_background()
                self.after(0, self._on_engine_started)
            except Exception as e:
                err = str(e)
                if "Access" in err:
                    err = "Run as Administrator"
                self.after(0, lambda: self._on_engine_failed(err))

        threading.Thread(target=_try_start, daemon=True).start()

    def _on_engine_started(self):
        self.engine_running = True
        self.power_btn.configure(
            text="STOP", fg_color=RED, hover_color="#dc2626", state="normal")
        self._set_status("Running")
        self._update_stats_loop()

    def _on_engine_failed(self, error: str):
        self._engine = None
        self.power_btn.configure(
            text="START", fg_color=GREEN, hover_color="#1aab50", state="normal")
        self._set_status(f"Error: {error}")

    def _stop_engine(self):
        if self._engine:
            self._engine.stop()
            self._engine = None

        self.engine_running = False
        self.power_btn.configure(text="START", fg_color=GREEN, hover_color="#1aab50")
        self._set_status("Idle")

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

    # ══════════════════════════════════════════════
    #  SETTINGS LOGIC
    # ══════════════════════════════════════════════

    def _format_rate(self, kbps: int) -> str:
        if kbps == 0:
            return "Disabled"
        elif kbps >= 1024:
            return f"{kbps/1024:.1f} MB/s"
        return f"{kbps} KB/s"

    def _on_dl_toggle(self):
        enabled = self.dl_toggle.get()
        if enabled:
            rate = int(self.dl_slider.get())
            self._config["download_kbps"] = rate
            self.dl_value_label.configure(text=self._format_rate(rate))
        else:
            self._config["download_kbps"] = 0
            self.dl_value_label.configure(text="Disabled")

        if self._engine:
            self._engine.set_download_bandwidth(self._config["download_kbps"])

        save_config(self._config)

    def _on_dl_slider_change(self, value):
        rate = int(value)
        self.dl_value_label.configure(text=self._format_rate(rate))

        if self.dl_toggle.get():
            self._config["download_kbps"] = rate
            if self._engine:
                self._engine.set_download_bandwidth(rate)

            if hasattr(self, "_dl_save_timer"):
                self.after_cancel(self._dl_save_timer)
            self._dl_save_timer = self.after(500, lambda: save_config(self._config))

    def _auto_detect_download(self):
        self.dl_detect_btn.configure(text="Testing...", state="disabled")

        def _measure():
            try:
                _, recommended, info = get_recommended_download(0.85)
                if recommended > 0:
                    slider_val = min(recommended, 200000)
                    self.after(0, lambda: self._apply_dl_detected(slider_val, info))
                else:
                    self.after(0, lambda: self._dl_detect_failed())
            except Exception:
                self.after(0, lambda: self._dl_detect_failed())

        threading.Thread(target=_measure, daemon=True).start()

    def _apply_dl_detected(self, kbps: int, info: str):
        self.dl_slider.set(kbps)
        self._on_dl_slider_change(kbps)
        self.dl_toggle.select()
        self._on_dl_toggle()
        self._set_status(info)
        self.dl_detect_btn.configure(text="Auto-detect", state="normal")

    def _dl_detect_failed(self):
        self._set_status("Download detection failed")
        self.dl_detect_btn.configure(text="Auto-detect", state="normal")

    def _on_slider_change(self, value):
        rate = int(value)
        if rate >= 1024:
            self.rate_value_label.configure(text=f"{rate/1024:.1f} MB/s")
        else:
            self.rate_value_label.configure(text=f"{rate} KB/s")

        self._config["bandwidth_kbps"] = rate
        if self._engine:
            self._engine.set_bandwidth(rate)

        # Debounce: نحفظ بعد ما يوقف المستخدم عن التحريك
        if hasattr(self, "_save_timer"):
            self.after_cancel(self._save_timer)
        self._save_timer = self.after(500, lambda: save_config(self._config))

    def _auto_detect_speed(self):
        self.auto_detect_btn.configure(text="Detecting...", state="disabled")

        def _measure():
            try:
                link_kbps, recommended, info = get_recommended_bandwidth(0.90)
                slider_val = min(recommended, 50000)
                self.after(0, lambda: self._apply_detected(slider_val, info))
            except Exception:
                self.after(0, lambda: self.auto_detect_btn.configure(
                    text="Auto-detect", state="normal"))

        threading.Thread(target=_measure, daemon=True).start()

    def _apply_detected(self, kbps: int, info: str):
        self.throttle_slider.set(kbps)
        self._on_slider_change(kbps)
        self._set_status(info)
        self.auto_detect_btn.configure(text="Auto-detect", state="normal")

    # ══════════════════════════════════════════════
    #  STATS UPDATE
    # ══════════════════════════════════════════════

    def _update_stats_loop(self):
        if not self.engine_running or not self._engine:
            return

        try:
            stats = self._engine.get_stats()

            # Active apps
            names = stats.get("active_priority_names", set())
            fast = stats.get("fast_path_sent", 0)
            if names:
                fast_str = f" (fast-path: {fast})" if fast else ""
                self.active_apps_label.configure(
                    text=", ".join(sorted(names)) + fast_str,
                    text_color=ACCENT,
                )
                self._set_status(f"Protecting {len(names)} app(s)")
            else:
                self.active_apps_label.configure(
                    text="None running — add rules in Priority Rules tab",
                    text_color=TEXT_DIM,
                )
                self._set_status("Running — no priority apps active")

            # Tin stats
            for tin_name in ("Voice", "High", "Normal", "Bulk"):
                if tin_name in stats:
                    s = stats[tin_name]
                    labels = self.tin_labels[tin_name]
                    labels["sent"].configure(text=str(s["sent"]))

                    kb = s["bytes_sent"] / 1024
                    kb_str = f"{kb/1024:.1f} MB" if kb > 1024 else f"{kb:.0f} KB"
                    extra = " | CoDel!" if s.get("codel_dropping") else ""
                    d = s["dropped"]
                    labels["info"].configure(
                        text=f"{kb_str} | D:{d}{extra}"
                    )

            # Totals
            if "total" in stats:
                t = stats["total"]
                self.total_sent_label.configure(text=str(t["sent"]))
                self.total_dropped_label.configure(text=str(t["dropped"]))
                self.drop_rate_label.configure(text=f"{t['drop_rate_pct']:.1f}%")

            self.captured_label.configure(text=str(stats.get("captured", 0)))

            # Download stats
            dl_dropped = stats.get("dl_dropped", 0)
            dl_total = stats.get("dl_total", 0)
            if dl_total > 0:
                dl_pct = (dl_dropped / dl_total) * 100
                self._set_status(
                    f"UL: {t['sent']} sent | DL: {dl_total} policed ({dl_pct:.1f}% drop)"
                )

        except Exception:
            pass

        self.after(1000, self._update_stats_loop)

    def _on_close(self):
        if self.engine_running:
            self._stop_engine()
        self.destroy()


def main():
    app = NetPilotGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
