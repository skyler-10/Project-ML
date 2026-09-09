from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")
matplotlib.rcParams["font.family"] = "sans-serif"
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from protocol import PacketStreamDecoder, missing_cycle_count
from runtime import MAX_DISPLAY_ROWS, SQLiteRecordStore, WaterQualityPredictor, build_record


LEVEL_STYLES = {
    "Normal": ("#E8F5E9", "#176B3A", "Normal｜正常", "水质状态正常"),
    "Caution": ("#FFE8A3", "#714800", "Caution｜注意", "请关注水质变化"),
    "Warning": ("#FFB45C", "#702A00", "Warning｜警告", "请尽快检查水质"),
    "Severe": ("#C62828", "#FFFFFF", "Severe｜严重警报", "严重异常，请立即处理"),
}
TREND_SERIES = (
    ("temp_C", "温度", "°C", "#D95D39"),
    ("pH", "pH", "", "#24745F"),
    ("tds_mgL", "TDS", "mg/L", "#1F6E9C"),
    ("ec_uScm", "电导率", "µS/cm", "#8A5A00"),
    ("do_mgL", "溶解氧", "mg/L", "#6A4C93"),
)
TREND_WINDOW_SIZE = 100
NORMAL_LIMITS = {
    "temp_C": (25.00, 29.96),
    "pH": (6.50, 8.50),
    "tds_mgL": (21.09, 323.72),
    "ec_uScm": (50.26, 499.73),
    "do_mgL": (5.02, 9.00),
}


class SerialReader(threading.Thread):
    def __init__(self, port: str, baudrate: int, events: Queue) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.baudrate = baudrate
        self.events = events
        self.stop_event = threading.Event()
        self.serial_port = None

    def run(self) -> None:
        try:
            import serial

            self.serial_port = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.2,
            )
            decoder = PacketStreamDecoder()
            self.events.put(("connection", True))
            while not self.stop_event.is_set():
                chunk = self.serial_port.read(self.serial_port.in_waiting or 1)
                invalid_before = decoder.invalid_frames
                packets = decoder.feed(chunk)
                invalid_delta = decoder.invalid_frames - invalid_before
                if invalid_delta:
                    self.events.put(("invalid_frames", invalid_delta))
                for packet in packets:
                    self.events.put(("packet", packet))
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            if self.serial_port is not None and self.serial_port.is_open:
                self.serial_port.close()
            self.events.put(("connection", False))

    def stop(self) -> None:
        self.stop_event.set()


class WaterQualityApp:
    def __init__(self, root: tk.Tk, port: str, baudrate: int, db_path: Path) -> None:
        self.root = root
        self.root.title("水质实时监测与智能分级")
        self.root.geometry("1080x700")
        self.root.minsize(900, 620)
        self.root.configure(bg="#F2F5F3")

        self.predictor = WaterQualityPredictor()
        self.store = SQLiteRecordStore(db_path, port, baudrate)
        self.events: Queue = Queue()
        self.reader: SerialReader | None = None
        self.port_var = tk.StringVar(value=port)
        self.baud_var = tk.StringVar(value=str(baudrate))
        self.connection_var = tk.StringVar(value="未连接")
        self.packet_count_var = tk.StringVar(value="有效包 0")
        self.lost_packet_var = tk.StringVar(value="丢包 0")
        self.invalid_frame_var = tk.StringVar(value="无效帧 0")
        self.last_received_var = tk.StringVar(value="最后接收 --:--:--")
        self.packet_count = 0
        self.lost_packet_count = 0
        self.invalid_frame_count = 0
        self.last_cycle_id: int | None = None
        self.value_vars = {
            "temp_C": tk.StringVar(value="--"),
            "pH": tk.StringVar(value="--"),
            "tds_mgL": tk.StringVar(value="--"),
            "ec_uScm": tk.StringVar(value="--"),
            "do_mgL": tk.StringVar(value="--"),
        }
        self.level_var = tk.StringVar(value="等待数据")
        self.level_detail_var = tk.StringVar(value="尚未收到数据")
        self.trend_window: tk.Toplevel | None = None
        self.trend_figure: Figure | None = None
        self.trend_canvas: FigureCanvasTkAgg | None = None
        self.trend_axes = []
        self.trend_count_var = tk.StringVar(value="当前会话 0 条")
        self.trend_window_size_var = tk.IntVar(value=TREND_WINDOW_SIZE)
        self.trend_data_lines = {}
        self.trend_annotations = {}
        self.history_window: tk.Toplevel | None = None
        self.history_table: ttk.Treeview | None = None
        self.history_session_var = tk.StringVar(value="全部会话")
        self.history_start_var = tk.StringVar()
        self.history_end_var = tk.StringVar()
        self.history_level_var = tk.StringVar(value="全部等级")
        self.history_result_var = tk.StringVar(value="共 0 条")
        self.history_session_lookup: dict[str, int | None] = {"全部会话": None}

        self._build_ui(db_path)
        self._load_history()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._process_events)

    def _build_ui(self, db_path: Path) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=28, font=("Microsoft YaHei", 10))
        style.configure("Treeview.Heading", font=("Microsoft YaHei", 10, "bold"))

        header = tk.Frame(self.root, bg="#153D35", padx=24, pady=18)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="水质实时监测",
            bg="#153D35",
            fg="white",
            font=("Microsoft YaHei", 22, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            header,
            textvariable=self.connection_var,
            bg="#153D35",
            fg="#BFE0D6",
            font=("Microsoft YaHei", 11),
        ).pack(side=tk.RIGHT)

        controls = tk.Frame(self.root, bg="#F2F5F3", padx=24, pady=14)
        controls.pack(fill=tk.X)
        tk.Label(controls, text="串口", bg="#F2F5F3", fg="#354A43", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 6))
        self.port_combo = ttk.Combobox(
            controls,
            textvariable=self.port_var,
            width=12,
            state="readonly",
            font=("Consolas", 10),
        )
        self.port_combo.pack(side=tk.LEFT, padx=(0, 6), ipady=4)
        tk.Button(
            controls,
            text="刷新串口",
            command=self.refresh_ports,
            bg="#DCE8E3",
            fg="#153D35",
            relief=tk.FLAT,
            padx=10,
            pady=6,
            font=("Microsoft YaHei", 9),
        ).pack(side=tk.LEFT, padx=(0, 14))
        self._field(controls, "波特率", self.baud_var, 12)
        self.connect_button = tk.Button(
            controls,
            text="连接",
            command=self.toggle_connection,
            bg="#24745F",
            fg="white",
            activebackground="#1B5B4A",
            activeforeground="white",
            relief=tk.FLAT,
            padx=20,
            pady=7,
            font=("Microsoft YaHei", 10, "bold"),
        )
        self.connect_button.pack(side=tk.LEFT, padx=(8, 6))
        tk.Button(
            controls,
            text="变化趋势",
            command=self.show_trend_window,
            bg="#DCE8E3",
            fg="#153D35",
            relief=tk.FLAT,
            padx=16,
            pady=7,
            font=("Microsoft YaHei", 10),
        ).pack(side=tk.LEFT)
        tk.Button(
            controls,
            text="历史查询",
            command=self.show_history_window,
            bg="#DCE8E3",
            fg="#153D35",
            relief=tk.FLAT,
            padx=16,
            pady=7,
            font=("Microsoft YaHei", 10),
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(
            controls,
            text=f"SQLite: {db_path.name}",
            bg="#F2F5F3",
            fg="#64746E",
            font=("Microsoft YaHei", 9),
        ).pack(side=tk.RIGHT)

        status_bar = tk.Frame(self.root, bg="#FFFFFF", padx=24, pady=9)
        status_bar.pack(fill=tk.X)
        status_items = (
            (self.packet_count_var, "#176B3A"),
            (self.lost_packet_var, "#A64000"),
            (self.invalid_frame_var, "#A51D2D"),
            (self.last_received_var, "#53635D"),
        )
        for index, (variable, color) in enumerate(status_items):
            tk.Label(
                status_bar,
                textvariable=variable,
                bg="#FFFFFF",
                fg=color,
                font=("Microsoft YaHei", 10, "bold"),
            ).pack(side=tk.LEFT, padx=(0 if index == 0 else 24, 0))

        cards = tk.Frame(self.root, bg="#F2F5F3", padx=18, pady=4)
        cards.pack(fill=tk.X)
        card_specs = [
            ("温度", "temp_C", "°C"),
            ("pH", "pH", ""),
            ("TDS", "tds_mgL", "mg/L"),
            ("电导率", "ec_uScm", "µS/cm"),
            ("溶解氧", "do_mgL", "mg/L"),
        ]
        for title, key, unit in card_specs:
            card = tk.Frame(cards, bg="white", padx=14, pady=12)
            card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
            tk.Label(card, text=title, bg="white", fg="#65736E", font=("Microsoft YaHei", 10)).pack(anchor="w")
            value_row = tk.Frame(card, bg="white")
            value_row.pack(anchor="w", pady=(8, 0))
            tk.Label(value_row, textvariable=self.value_vars[key], bg="white", fg="#172A25", font=("Microsoft YaHei", 20, "bold")).pack(side=tk.LEFT)
            tk.Label(value_row, text=f" {unit}", bg="white", fg="#65736E", font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, anchor="s", pady=3)

        level_area = tk.Frame(self.root, bg="#F2F5F3", padx=24, pady=16)
        level_area.pack(fill=tk.X)
        self.level_panel = tk.Frame(level_area, bg="#E8EEEB", padx=22, pady=14)
        self.level_panel.pack(fill=tk.X)
        tk.Label(self.level_panel, text="模型判定", bg="#E8EEEB", fg="#65736E", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT)
        self.level_label = tk.Label(self.level_panel, textvariable=self.level_var, bg="#E8EEEB", fg="#243832", font=("Microsoft YaHei", 19, "bold"))
        self.level_label.pack(side=tk.LEFT, padx=28)
        self.level_detail_label = tk.Label(self.level_panel, textvariable=self.level_detail_var, bg="#E8EEEB", fg="#65736E", font=("Microsoft YaHei", 11, "bold"))
        self.level_detail_label.pack(side=tk.RIGHT)

        table_frame = tk.Frame(self.root, bg="#F2F5F3", padx=24, pady=4)
        table_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("time", "sample", "temp", "ph", "tds", "ec", "do", "level")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        labels = ("电脑时间", "序号", "温度", "pH", "TDS", "EC", "DO", "等级")
        widths = (155, 60, 75, 70, 75, 90, 75, 145)
        for column, label, width in zip(columns, labels, widths):
            self.table.heading(column, text=label)
            self.table.column(column, width=width, anchor=tk.CENTER)
        self.table.tag_configure("Normal", background="#F1F8F3", foreground="#176B3A")
        self.table.tag_configure("Caution", background="#FFF3CD", foreground="#714800")
        self.table.tag_configure("Warning", background="#FFE0B2", foreground="#702A00")
        self.table.tag_configure("Severe", background="#C62828", foreground="#FFFFFF")
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.refresh_ports()

    @staticmethod
    def _field(parent: tk.Widget, label: str, variable: tk.StringVar, width: int) -> None:
        tk.Label(parent, text=label, bg="#F2F5F3", fg="#354A43", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 6))
        tk.Entry(parent, textvariable=variable, width=width, relief=tk.SOLID, bd=1, font=("Consolas", 10)).pack(side=tk.LEFT, padx=(0, 14), ipady=5)

    def toggle_connection(self) -> None:
        if self.reader is not None and self.reader.is_alive():
            self.reader.stop()
            return
        try:
            baudrate = int(self.baud_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "波特率必须是整数。")
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("参数错误", "未检测到可用串口，请连接设备后刷新串口。")
            return
        self.connection_var.set("正在连接...")
        self.last_cycle_id = None
        self.store.update_connection(port, baudrate)
        self.reader = SerialReader(port, baudrate, self.events)
        self.reader.start()

    def refresh_ports(self) -> None:
        from serial.tools import list_ports

        ports = [port.device for port in list_ports.comports()]
        current = self.port_var.get()
        self.port_combo["values"] = ports
        if current in ports:
            self.port_var.set(current)
        elif ports:
            self.port_var.set(ports[0])
        else:
            self.port_var.set("")

    def show_history_window(self) -> None:
        if self.history_window is not None and self.history_window.winfo_exists():
            self.history_window.deiconify()
            self.history_window.lift()
            self._refresh_history_sessions()
            self._query_history()
            return

        self.history_window = tk.Toplevel(self.root)
        self.history_window.title("水质历史数据查询")
        self.history_window.geometry("1240x720")
        self.history_window.minsize(1120, 620)
        self.history_window.configure(bg="#F2F5F3")
        self.history_window.protocol("WM_DELETE_WINDOW", self.history_window.withdraw)

        header = tk.Frame(self.history_window, bg="#153D35", padx=22, pady=14)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="历史监测记录",
            bg="#153D35",
            fg="#FFFFFF",
            font=("Microsoft YaHei", 17, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            header,
            textvariable=self.history_result_var,
            bg="#153D35",
            fg="#BFE0D6",
            font=("Microsoft YaHei", 10),
        ).pack(side=tk.RIGHT)

        filters = tk.Frame(self.history_window, bg="#F2F5F3", padx=22, pady=14)
        filters.pack(fill=tk.X)
        tk.Label(filters, text="会话", bg="#F2F5F3", fg="#354A43").pack(side=tk.LEFT)
        self.history_session_combo = ttk.Combobox(
            filters,
            textvariable=self.history_session_var,
            width=35,
            state="readonly",
        )
        self.history_session_combo.pack(side=tk.LEFT, padx=(6, 16), ipady=3)
        self._history_field(filters, "开始日期", self.history_start_var)
        self._history_field(filters, "结束日期", self.history_end_var)
        tk.Label(filters, text="等级", bg="#F2F5F3", fg="#354A43").pack(side=tk.LEFT)
        ttk.Combobox(
            filters,
            textvariable=self.history_level_var,
            values=("全部等级", "Normal", "Caution", "Warning", "Severe"),
            width=12,
            state="readonly",
        ).pack(side=tk.LEFT, padx=(6, 16), ipady=3)
        tk.Button(
            filters,
            text="查询",
            command=self._query_history,
            bg="#24745F",
            fg="#FFFFFF",
            relief=tk.FLAT,
            padx=18,
            pady=6,
            font=("Microsoft YaHei", 9, "bold"),
        ).pack(side=tk.LEFT)
        tk.Button(
            filters,
            text="重置",
            command=self._reset_history_filters,
            bg="#DCE8E3",
            fg="#153D35",
            relief=tk.FLAT,
            padx=14,
            pady=6,
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(
            filters,
            text="导出CSV",
            command=self._export_history_csv,
            bg="#DCE8E3",
            fg="#153D35",
            relief=tk.FLAT,
            padx=14,
            pady=6,
        ).pack(side=tk.RIGHT)

        table_frame = tk.Frame(self.history_window, bg="#F2F5F3", padx=22, pady=4)
        table_frame.pack(fill=tk.BOTH, expand=True)
        columns = (
            "time", "session", "sample", "temp", "ph", "tds", "ec", "do",
            "level", "confidence",
        )
        self.history_table = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=18
        )
        labels = (
            "电脑时间", "会话", "序号", "温度", "pH", "TDS", "EC", "DO",
            "等级", "置信度",
        )
        widths = (205, 65, 65, 75, 70, 80, 95, 80, 100, 80)
        for column, label, width in zip(columns, labels, widths):
            self.history_table.heading(column, text=label)
            self.history_table.column(column, width=width, anchor=tk.CENTER)
        for level, (background, foreground, _, _) in LEVEL_STYLES.items():
            self.history_table.tag_configure(
                level, background=background, foreground=foreground
            )
        vertical = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.history_table.yview
        )
        horizontal = ttk.Scrollbar(
            table_frame, orient=tk.HORIZONTAL, command=self.history_table.xview
        )
        self.history_table.configure(
            yscrollcommand=vertical.set, xscrollcommand=horizontal.set
        )
        self.history_table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        tk.Label(
            self.history_window,
            text="日期格式：YYYY-MM-DD。界面最多显示最新5000条，CSV导出包含全部筛选结果。",
            bg="#F2F5F3",
            fg="#64746E",
            font=("Microsoft YaHei", 9),
            padx=22,
            pady=10,
        ).pack(fill=tk.X)
        self._refresh_history_sessions()
        self._query_history()

    @staticmethod
    def _history_field(
        parent: tk.Widget, label: str, variable: tk.StringVar
    ) -> None:
        tk.Label(parent, text=label, bg="#F2F5F3", fg="#354A43").pack(side=tk.LEFT)
        tk.Entry(
            parent,
            textvariable=variable,
            width=12,
            relief=tk.SOLID,
            bd=1,
            font=("Consolas", 10),
        ).pack(side=tk.LEFT, padx=(6, 16), ipady=5)

    def _refresh_history_sessions(self) -> None:
        sessions = self.store.list_sessions()
        lookup: dict[str, int | None] = {"全部会话": None}
        for session in sessions:
            started_at = str(session["started_at"]).replace("T", " ")[:19]
            port = str(session["serial_port"] or "未连接")
            label = (
                f"#{session['id']} | {started_at} | {port} | "
                f"{session['record_count']}条"
            )
            lookup[label] = int(session["id"])
        current = self.history_session_var.get()
        self.history_session_lookup = lookup
        self.history_session_combo["values"] = list(lookup)
        self.history_session_var.set(current if current in lookup else "全部会话")

    def _history_filter_values(self) -> tuple[int | None, str, str, str] | None:
        start_date = self.history_start_var.get().strip()
        end_date = self.history_end_var.get().strip()
        try:
            for value in (start_date, end_date):
                if value:
                    datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("日期错误", "日期必须使用 YYYY-MM-DD 格式。")
            return None
        if start_date and end_date and start_date > end_date:
            messagebox.showerror("日期错误", "开始日期不能晚于结束日期。")
            return None
        session_id = self.history_session_lookup.get(self.history_session_var.get())
        level = self.history_level_var.get()
        return session_id, start_date, end_date, "" if level == "全部等级" else level

    def _query_history(self) -> None:
        if self.history_table is None:
            return
        filters = self._history_filter_values()
        if filters is None:
            return
        rows = self.store.query_history(*filters, limit=5000)
        self.history_table.delete(*self.history_table.get_children())
        for record in rows:
            level = str(record["predicted_level"])
            self.history_table.insert(
                "",
                tk.END,
                values=(
                    str(record["computer_time"]).replace("T", " "),
                    record["session_id"],
                    record["sample_id"],
                    f"{float(record['temp_C']):.2f}",
                    f"{float(record['pH']):.2f}",
                    record["tds_mgL"],
                    f"{float(record['ec_uScm']):.2f}",
                    f"{float(record['do_mgL']):.2f}",
                    level,
                    f"{float(record['confidence']):.4f}",
                ),
                tags=(level,),
            )
        suffix = "（显示上限5000条）" if len(rows) == 5000 else ""
        self.history_result_var.set(f"共 {len(rows)} 条{suffix}")

    def _reset_history_filters(self) -> None:
        self.history_session_var.set("全部会话")
        self.history_start_var.set("")
        self.history_end_var.set("")
        self.history_level_var.set("全部等级")
        self._refresh_history_sessions()
        self._query_history()

    def _export_history_csv(self) -> None:
        filters = self._history_filter_values()
        if filters is None:
            return
        rows = self.store.query_history(*filters)
        if not rows:
            messagebox.showinfo("没有数据", "当前筛选条件下没有可导出的记录。")
            return
        file_path = filedialog.asksaveasfilename(
            parent=self.history_window,
            title="导出历史数据",
            initialdir=self.store.file_path.parent,
            initialfile=f"water_quality_{datetime.now():%Y%m%d_%H%M%S}.csv",
            defaultextension=".csv",
            filetypes=(("CSV 文件", "*.csv"),),
        )
        if not file_path:
            return
        count = self.store.export_csv(Path(file_path), rows)
        messagebox.showinfo("导出完成", f"已导出 {count} 条记录。")

    def show_trend_window(self) -> None:
        if self.trend_window is not None and self.trend_window.winfo_exists():
            self.trend_window.deiconify()
            self.trend_window.lift()
            self._redraw_trends()
            return

        self.trend_window = tk.Toplevel(self.root)
        self.trend_window.title("水质特征变化趋势")
        self.trend_window.geometry("1100x760")
        self.trend_window.minsize(900, 650)
        self.trend_window.configure(bg="#F2F5F3")
        self.trend_window.protocol("WM_DELETE_WINDOW", self.trend_window.withdraw)

        header = tk.Frame(self.trend_window, bg="#153D35", padx=22, pady=14)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="五项水质特征变化趋势",
            bg="#153D35",
            fg="#FFFFFF",
            font=("Microsoft YaHei", 17, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            header,
            textvariable=self.trend_count_var,
            bg="#153D35",
            fg="#BFE0D6",
            font=("Microsoft YaHei", 10),
        ).pack(side=tk.RIGHT)
        range_selector = tk.Frame(header, bg="#153D35")
        range_selector.pack(side=tk.RIGHT, padx=(0, 24))
        tk.Label(
            range_selector,
            text="显示点数",
            bg="#153D35",
            fg="#BFE0D6",
            font=("Microsoft YaHei", 9),
        ).pack(side=tk.LEFT, padx=(0, 8))
        for point_count in (30, 50, 100):
            tk.Radiobutton(
                range_selector,
                text=str(point_count),
                variable=self.trend_window_size_var,
                value=point_count,
                command=self._redraw_trends,
                indicatoron=False,
                width=4,
                bg="#28594E",
                fg="#FFFFFF",
                selectcolor="#24745F",
                activebackground="#327063",
                activeforeground="#FFFFFF",
                relief=tk.FLAT,
                font=("Microsoft YaHei", 9, "bold"),
            ).pack(side=tk.LEFT, padx=2, ipady=3)

        chart_frame = tk.Frame(self.trend_window, bg="#F2F5F3", padx=16, pady=14)
        chart_frame.pack(fill=tk.BOTH, expand=True)
        self.trend_figure = Figure(figsize=(11, 7), dpi=100, facecolor="#F2F5F3")
        self.trend_axes = [self.trend_figure.add_subplot(3, 2, index + 1) for index in range(5)]
        unused_axis = self.trend_figure.add_subplot(3, 2, 6)
        unused_axis.axis("off")
        self.trend_figure.subplots_adjust(
            left=0.08, right=0.98, top=0.96, bottom=0.08, wspace=0.25, hspace=0.48
        )
        self.trend_canvas = FigureCanvasTkAgg(self.trend_figure, master=chart_frame)
        self.trend_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.trend_canvas.mpl_connect("motion_notify_event", self._on_trend_motion)
        self._redraw_trends()

    def _redraw_trends(self) -> None:
        if self.trend_canvas is None or not self.trend_axes:
            return
        window_size = self.trend_window_size_var.get()
        session_records = self.store.read(limit=MAX_DISPLAY_ROWS)
        records = list(reversed(session_records[:window_size]))
        self.trend_count_var.set(
            f"当前会话 {len(session_records)} 条｜显示最近 {len(records)} 条"
        )
        latest_sample_id = int(records[-1]["sample_id"]) if records else window_size
        window_start = max(1, latest_sample_id - window_size + 1)
        window_end = window_start + window_size - 1
        self.trend_data_lines.clear()
        self.trend_annotations.clear()

        for axis, (key, title, unit, color) in zip(self.trend_axes, TREND_SERIES):
            axis.clear()
            axis.set_facecolor("#FFFFFF")
            lower, upper = NORMAL_LIMITS[key]
            range_text = f"{lower:g}–{upper:g}{(' ' + unit) if unit else ''}"
            axis.set_title(
                f"{title}  正常范围 {range_text}",
                fontsize=10,
                fontweight="bold",
                color="#243832",
            )
            axis.set_xlabel("采样序号", fontsize=9)
            axis.set_ylabel(unit, fontsize=9)
            axis.grid(True, color="#D9E2DE", linewidth=0.7, alpha=0.8)
            axis.set_xlim(window_start, window_end)
            axis.axhspan(lower, upper, color="#66BB6A", alpha=0.12)
            axis.axhline(lower, color="#388E3C", linewidth=0.9, linestyle="--")
            axis.axhline(upper, color="#388E3C", linewidth=0.9, linestyle="--")
            if records:
                sample_ids = [int(record["sample_id"]) for record in records]
                values = [float(record[key]) for record in records]
                data_line, = axis.plot(
                    sample_ids, values, color=color, linewidth=1.8, marker="o",
                    markersize=3, markerfacecolor="#FFFFFF", markeredgewidth=0.8,
                    picker=6,
                )
                self.trend_data_lines[axis] = (data_line, title, unit)
                axis.scatter(sample_ids[-1], values[-1], color=color, s=28, zorder=3)
            else:
                axis.text(
                    0.5,
                    0.5,
                    "等待串口数据",
                    transform=axis.transAxes,
                    ha="center",
                    va="center",
                    color="#7A8984",
                    fontsize=10,
                )
            axis.tick_params(labelsize=8, colors="#53635D")
            annotation = axis.annotate(
                "",
                xy=(0, 0),
                xytext=(12, 12),
                textcoords="offset points",
                bbox={"boxstyle": "round,pad=0.4", "fc": "#153D35", "ec": "none", "alpha": 0.94},
                color="#FFFFFF",
                fontsize=9,
                arrowprops={"arrowstyle": "->", "color": "#153D35"},
                zorder=5,
            )
            annotation.set_visible(False)
            self.trend_annotations[axis] = annotation

        self.trend_canvas.draw_idle()

    def _on_trend_motion(self, event) -> None:
        if self.trend_canvas is None:
            return
        changed = False
        for axis, annotation in self.trend_annotations.items():
            line_info = self.trend_data_lines.get(axis)
            visible = False
            if event.inaxes is axis and line_info is not None:
                line, title, unit = line_info
                contains, details = line.contains(event)
                hit_indices = details.get("ind", [])
                if contains and len(hit_indices) > 0:
                    x_data = line.get_xdata()
                    y_data = line.get_ydata()
                    point_index = min(
                        (int(index) for index in hit_indices),
                        key=lambda index: sum(
                            (coordinate - cursor) ** 2
                            for coordinate, cursor in zip(
                                axis.transData.transform((x_data[index], y_data[index])),
                                (event.x, event.y),
                            )
                        ),
                    )
                    sample_id = int(x_data[point_index])
                    value = float(y_data[point_index])
                    annotation.xy = (sample_id, value)
                    unit_text = f" {unit}" if unit else ""
                    annotation.set_text(
                        f"采样 #{sample_id}\n{title}: {value:.2f}{unit_text}"
                    )
                    visible = True
            if annotation.get_visible() != visible:
                annotation.set_visible(visible)
                changed = True
        if changed:
            self.trend_canvas.draw_idle()

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "packet":
                    self._register_packet(payload.cycle_id)
                    self._handle_packet(payload)
                elif event == "connection":
                    connected = bool(payload)
                    if connected:
                        self.connection_var.set(
                            f"已连接 · {self.port_var.get()} · {self.baud_var.get()}"
                        )
                    else:
                        self.connection_var.set("未连接")
                    self.connect_button.configure(text="断开" if connected else "连接")
                elif event == "invalid_frames":
                    self.invalid_frame_count += int(payload)
                    self.invalid_frame_var.set(f"无效帧 {self.invalid_frame_count}")
                elif event == "error":
                    messagebox.showerror("串口错误", str(payload))
        except Empty:
            pass
        self.root.after(100, self._process_events)

    def _register_packet(self, cycle_id: int) -> None:
        self.packet_count += 1
        self.lost_packet_count += missing_cycle_count(self.last_cycle_id, cycle_id)
        self.last_cycle_id = cycle_id
        self.packet_count_var.set(f"有效包 {self.packet_count}")
        self.lost_packet_var.set(f"丢包 {self.lost_packet_count}")
        self.last_received_var.set(f"最后接收 {datetime.now():%H:%M:%S}")

    def _handle_packet(self, packet) -> None:
        level, confidence = self.predictor.predict(packet)
        record = build_record(packet, level, confidence)
        stored_record = self.store.append(record)
        self._display_record(stored_record, insert_row=True)
        self._redraw_trends()

    def _load_history(self) -> None:
        records = self.store.read(limit=MAX_DISPLAY_ROWS)
        if records:
            self._display_record(records[0], insert_row=False)
        for record in reversed(records):
            self._insert_table_row(record)

    def _display_record(self, record: dict[str, object], insert_row: bool) -> None:
        values = {
            "temp_C": f"{float(record['temp_C']):.2f}",
            "pH": f"{float(record['pH']):.2f}",
            "tds_mgL": str(record["tds_mgL"]),
            "ec_uScm": f"{float(record['ec_uScm']):.2f}",
            "do_mgL": f"{float(record['do_mgL']):.2f}",
        }
        for key, value in values.items():
            self.value_vars[key].set(value)
        level = str(record["predicted_level"])
        background, foreground, display_level, detail = LEVEL_STYLES.get(
            level, ("#E8EEEB", "#243832", level, "未知水质状态")
        )
        self.level_var.set(display_level)
        self.level_detail_var.set(detail)
        self.level_panel.configure(bg=background)
        for label in (self.level_label, self.level_detail_label):
            label.configure(bg=background)
        self.level_label.configure(fg=foreground)
        self.level_detail_label.configure(fg=foreground)
        if insert_row:
            self._insert_table_row(record)

    def _insert_table_row(self, record: dict[str, object]) -> None:
        self.table.insert(
            "",
            0,
            values=(
                str(record["computer_time"])[11:23],
                record["sample_id"],
                f"{float(record['temp_C']):.2f}",
                f"{float(record['pH']):.2f}",
                record["tds_mgL"],
                f"{float(record['ec_uScm']):.2f}",
                f"{float(record['do_mgL']):.2f}",
                LEVEL_STYLES.get(str(record["predicted_level"]), ("", "", str(record["predicted_level"]), ""))[2],
            ),
            tags=(str(record["predicted_level"]),),
        )
        children = self.table.get_children()
        for item in children[MAX_DISPLAY_ROWS:]:
            self.table.delete(item)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader.join(timeout=1.0)
        while True:
            try:
                event, payload = self.events.get_nowait()
            except Empty:
                break
            if event == "packet":
                self._register_packet(payload.cycle_id)
                self._handle_packet(payload)
        self.store.close()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Water quality serial monitor")
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(__file__).resolve().parent / "water_quality.db",
    )
    args = parser.parse_args()

    root = tk.Tk()
    WaterQualityApp(root, args.port, args.baud, args.db)
    root.mainloop()


if __name__ == "__main__":
    main()