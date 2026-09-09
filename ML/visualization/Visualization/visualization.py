import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from datetime import datetime
import pandas as pd
import matplotlib
matplotlib.use("TkAgg")
matplotlib.rcParams["font.family"] = "sans-serif"
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"]
matplotlib.rcParams["axes.unicode_minus"] = False
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# ========== 配置区域 ==========
BASE_DIR = Path(__file__).resolve().parent
EXCEL_FILE = BASE_DIR / "waterdata.xlsx"   # Excel 文件名/路径（相对当前脚本）
ID_COL     = "id"
COL_DEPTH  = "Depth(m)"
COL_TEMP   = "Temperatur(degrees C)"
COL_SAL    = "Salinity"
COL_DO     = "DO(mg/L)"
COL_PH     = "pH"
COL_QL     = "QualityLevel"  # 可选：如果有质量列，可以在界面显示或用于过滤

# 阈值配置（可根据需要调整）
THRESHOLDS = {
    "depth":   (0, 1000),      # 深度范围：0 ~ 1000 m
    "temp":    (15, 35),       # 温度范围：15 ~ 35 °C
    "sal":     (0, 40),        # 盐度范围：0 ~ 40
    "do":  (0, 10),       # 含氧量范围：0 ~ 10 mg/L
    "ph":      (7.0, 8.5),     # pH 范围：7.0 ~ 8.5
}

WINDOW_SIZE      = 30    # 二级界面趋势图显示最近多少条数据
AUTO_INTERVAL_MS = 1000  # 自动读取间隔：1 秒
# =============================


class DataVisualizerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("水质数据可视化")
        self.root.geometry("980x720")
        self.root.minsize(920, 680)
        self.root.configure(bg="#eef3f8")

        # 读取 Excel 数据
        self.df = self.load_data(EXCEL_FILE)
        if self.df is None or self.df.empty:
            messagebox.showerror("错误", "数据为空或读取失败")
            self.root.destroy()
            return

        # 当前记录索引（0-based）
        self.current_idx = 0

        # 趋势图缓存：历史 id 和各变量值
        self.x_ids = []
        self.depth_values = []
        self.temp_values = []
        self.sal_values = []
        self.do_values = []
        self.ph_values = []
        self.ql_values = []
        self.alarm_flags = {
            "depth": [],
            "temp": [],
            "sal": [],
            "do": [],
            "ph": [],
            "any": []
        }

        # 自动模式状态
        self.is_auto_running = False
        self.auto_job_id = None
        self.log_text = None
        self.log_entries = []
        self.hide_normal_logs = False
        self.log_toggle_text = tk.StringVar(value="隐藏无报警信息")
        self.status_cards = {}

        # 创建界面
        self.create_main_widgets()
        self.plot_window = None
        # 一开始不创建趋势图窗口
        self.plot_window = None
        self.fig = None
        self.ax_depth = None
        self.ax_temp = None
        self.ax_sal = None
        self.ax_do = None
        self.ax_ph = None
        self.ax_ql = None
        # 显示第一条记录
        self.update_display()

    # ------------------- 数据读取 -------------------
    def load_data(self, file_path):
        try:
            if not Path(file_path).exists():
                raise FileNotFoundError(f"未找到文件: {file_path}")

            df = pd.read_excel(file_path)

            required_cols = [ID_COL, COL_DEPTH, COL_TEMP,
                             COL_SAL, COL_DO, COL_PH, COL_QL]
            for col in required_cols:
                if col not in df.columns:
                    raise ValueError(f"缺少列: {col}")

            # 按 id 排序
            df = df.sort_values(by=ID_COL).reset_index(drop=True)
            return df

        except Exception as e:
            messagebox.showerror("错误", f"读取 Excel 失败\n文件路径: {file_path}\n原因: {e}")
            return None

    # ------------------- 主界面布局（pack 优化） -------------------
    def create_main_widgets(self):
        # 整个主界面最外层 Frame
        style = ttk.Style()
        style.theme_use("clam")

        big_font = ("Microsoft YaHei", 18, "bold")
        label_font = ("Microsoft YaHei", 12)

        style.configure("App.TFrame", background="#eef3f8")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure(
            "Header.TLabel",
            background="#ffffff",
            foreground="#1f2937",
            font=("Microsoft YaHei", 12, "bold")
        )
        style.configure(
            "Record.TLabel",
            background="#ffffff",
            foreground="#0f172a",
            font=("Microsoft YaHei", 12, "bold")
        )
        style.configure(
            "Hint.TLabel",
            background="#ffffff",
            foreground="#64748b",
            font=("Microsoft YaHei", 10)
        )
        style.configure(
            "BigValue.TEntry",
            font=big_font,
            padding=8
        )
        style.configure(
            "BigValue.TLabel",
            background="#ffffff",
            foreground="#334155",
            font=label_font
        )
        style.configure(
            "Action.TButton",
            font=("Microsoft YaHei", 10, "bold"),
            padding=(10, 6)
        )
        style.map("Action.TButton", background=[("active", "#dbeafe")])
        style.configure(
            "Status.TLabel",
            background="#ffffff",
            foreground="#2563eb",
            font=("Microsoft YaHei", 10, "bold")
        )
        main_frame = ttk.Frame(self.root, padding=16, style="App.TFrame")
        main_frame.pack(fill=tk.BOTH, expand=True)

         # 状态2：两种颜色样式
        style.configure(
            "OKStatus.TLabel",
            foreground="green",
            background="#ffffff",
            font=("Microsoft YaHei", 11)
        )
        style.configure(
            "AlarmStatus.TLabel",
            foreground="red",
            background="#ffffff",
            font=("Microsoft YaHei", 11, "bold")
        )
        
        # ===== 顶部：记录信息 + 按钮条 =====
        top_frame = ttk.Frame(main_frame, style="Card.TFrame", padding=(14, 12))
        top_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))

        # 左侧：当前记录信息
        self.label_record = ttk.Label(top_frame, text="当前记录: 0 / 0", style="Record.TLabel")
        self.label_record.pack(side=tk.LEFT, padx=5)

        # 右侧：按钮条
        btn_frame = ttk.Frame(top_frame, style="Card.TFrame")
        btn_frame.pack(side=tk.RIGHT)

        btn_prev = ttk.Button(btn_frame, text="上一条", command=self.prev_record, style="Action.TButton")
        btn_prev.pack(side=tk.LEFT, padx=3)

        btn_next = ttk.Button(btn_frame, text="下一条", command=self.next_record, style="Action.TButton")
        btn_next.pack(side=tk.LEFT, padx=3)

        btn_plot = ttk.Button(btn_frame, text="趋势图窗口",
                              command=self.show_plot_window, style="Action.TButton")
        btn_plot.pack(side=tk.LEFT, padx=3)

        btn_auto_start = ttk.Button(btn_frame, text="开始自动读取",
                                    command=self.start_auto, style="Action.TButton")
        btn_auto_start.pack(side=tk.LEFT, padx=3)

        btn_auto_stop = ttk.Button(btn_frame, text="停止自动读取",
                                   command=self.stop_auto, style="Action.TButton")
        btn_auto_stop.pack(side=tk.LEFT, padx=3)

        # ===== 中部：五个数值框（竖直对齐） =====
        middle_frame = ttk.Frame(main_frame, style="Card.TFrame", padding=(14, 14))
        middle_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))

        # 左列和右列两个大列 Frame
        left_col  = ttk.Frame(middle_frame, style="Card.TFrame")
        right_col = ttk.Frame(middle_frame, style="Card.TFrame")
        left_col.pack(side=tk.LEFT,  fill=tk.X, expand=True)
        right_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(20, 0))

        def create_value_row(parent, label_text):
            row = ttk.Frame(parent, style="Card.TFrame")
            row.pack(side=tk.TOP, fill=tk.X, pady=3)
            label = ttk.Label(row, text=label_text, width=18, anchor=tk.W, style="BigValue.TLabel")
            label.pack(side=tk.LEFT, padx=(5, 2))
            var = tk.StringVar()
            entry = ttk.Entry(row, textvariable=var, width=18, state="readonly", style="BigValue.TEntry")
            entry.pack(side=tk.LEFT, padx=5)
            return var

        # 第 1 行：左 深度，右 温度
        self.var_depth = create_value_row(left_col,  "深度 Depth(m)")
        self.var_temp  = create_value_row(right_col, "温度 Temperatur")

        # 第 2 行：左 盐度，右 含氧量
        self.var_sal   = create_value_row(left_col,  "盐度 Salinity")
        self.var_do    = create_value_row(right_col, "含氧量 DO")

        # 第 3 行：左 pH，右 水质等级
        self.var_ph    = create_value_row(left_col,  "pH")
        self.var_ql    = create_value_row(right_col, "水质等级 QualityLevel")

        # ===== 底部：状态信息 =====
        bottom_frame = ttk.Frame(main_frame, style="Card.TFrame", padding=(14, 12))
        bottom_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.var_status1 = tk.StringVar(value="就绪")
        self.var_status1_detail = tk.StringVar(value="等待首次读取")
        self.var_status2 = tk.StringVar(value="等待操作")
        self.var_status2_detail = tk.StringVar(value="尚未开始监测")
        self.var_status_time = tk.StringVar(value="--:--:--")
        self.var_status_time_detail = tk.StringVar(value="最近更新时间")

        status_panel = tk.Frame(bottom_frame, bg="#ffffff", bd=0, highlightthickness=0)
        status_panel.pack(side=tk.TOP, fill=tk.X, pady=(2, 12))

        self.status_cards["read"] = self.create_status_card(
            status_panel,
            "数据读取",
            self.var_status1,
            self.var_status1_detail,
            bg_color="#eff6ff",
            accent_color="#2563eb"
        )
        self.status_cards["run"] = self.create_status_card(
            status_panel,
            "运行监测",
            self.var_status2,
            self.var_status2_detail,
            bg_color="#f0fdf4",
            accent_color="#16a34a"
        )
        self.status_cards["time"] = self.create_status_card(
            status_panel,
            "最近更新",
            self.var_status_time,
            self.var_status_time_detail,
            bg_color="#f8fafc",
            accent_color="#64748b",
            is_last=True
        )

        log_header = ttk.Frame(bottom_frame, style="Card.TFrame")
        log_header.pack(side=tk.TOP, fill=tk.X, pady=(12, 6))

        ttk.Label(log_header, text="运行日志", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            log_header,
            text="显示每次读取状态、工作模式和报警信息",
            style="Hint.TLabel"
        ).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(
            log_header,
            textvariable=self.log_toggle_text,
            command=self.toggle_log_filter,
            style="Action.TButton"
        ).pack(side=tk.RIGHT)

        log_frame = ttk.Frame(bottom_frame, style="Card.TFrame")
        log_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        log_scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(
            log_frame,
            height=12,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            bg="#0f172a",
            fg="#e2e8f0",
            insertbackground="#e2e8f0",
            relief="flat",
            padx=10,
            pady=8,
            yscrollcommand=log_scrollbar.set
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("timestamp", foreground="#7dd3fc")
        self.log_text.tag_configure("normal", foreground="#e2e8f0")
        self.log_text.tag_configure("alarm", foreground="#fca5a5")
        log_scrollbar.config(command=self.log_text.yview)

    # ------------------- 二级界面：趋势图窗口 -------------------
    def create_plot_window(self):
        self.plot_window = tk.Toplevel(self.root)
        self.plot_window.title("数据趋势图")
        self.plot_window.geometry("1400x800")
        self.plot_window.configure(bg="#eef3f8")
        self.plot_window.protocol("WM_DELETE_WINDOW", self.hide_plot_window)

        # 外层 Frame：上(说明文字) + 下(图形区域)
        outer = ttk.Frame(self.plot_window, padding=10, style="App.TFrame")
        outer.pack(fill=tk.BOTH, expand=True)

        # 顶部说明栏
        top_bar = ttk.Frame(outer, style="Card.TFrame", padding=(12, 10))
        top_bar.pack(side=tk.TOP, fill=tk.X)

        info_label = ttk.Label(
            top_bar,
            text="趋势图（显示最近 30 条记录，红色星号表示报警点）",
            style="Header.TLabel"
        )
        info_label.pack(side=tk.LEFT)

        # 图形区域
        figure_frame = ttk.Frame(outer, style="Card.TFrame", padding=(8, 8))
        figure_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Figure：3 行 2 列布局，共 6 张图
        self.fig = Figure(figsize=(11, 8), dpi=100)
        self.fig.patch.set_facecolor("#f8fafc")

        self.ax_depth = self.fig.add_subplot(321)   # row1 col1 深度
        self.ax_temp  = self.fig.add_subplot(322)   # row1 col2 温度
        self.ax_sal   = self.fig.add_subplot(323)   # row2 col1 盐度
        self.ax_do    = self.fig.add_subplot(324)   # row2 col2 含氧量
        self.ax_ph    = self.fig.add_subplot(325)   # row3 col1 pH
        self.ax_ql    = self.fig.add_subplot(326)   # row3 col2 水质等级

        # 初始标签（真正绘制时在 redraw_plots 再设置一次）
        self.ax_depth.set_ylabel("Depth(m)")
        self.ax_temp.set_ylabel("Temperature(°C)")
        self.ax_sal.set_ylabel("Salinity")
        self.ax_do.set_ylabel("DO(mg/L)")
        self.ax_ph.set_ylabel("pH")
        self.ax_ql.set_ylabel("Water Quality Level")
        
        for ax in [self.ax_depth, self.ax_temp, self.ax_sal,
                self.ax_do, self.ax_ph, self.ax_ql]:
            ax.grid(True, linestyle="--", alpha=0.3)

        # 调整子图间距和边缘
        self.fig.subplots_adjust(left=0.09, right=0.98,
                                top=0.96, bottom=0.06,
                                wspace=0.30, hspace=0.35)

        # 嵌入 Tkinter
        self.canvas = FigureCanvasTkAgg(self.fig, master=figure_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        

    def hide_plot_window(self):
        if self.plot_window is not None:
            self.plot_window.withdraw()

    def show_plot_window(self):
        if self.plot_window is None or not self.plot_window.winfo_exists():
            self.create_plot_window()

         # 显示并置顶
        self.plot_window.deiconify()
        self.plot_window.lift()

    def create_status_card(self, parent, title, value_var, detail_var,
                           bg_color, accent_color, is_last=False):
        card = tk.Frame(parent, bg=bg_color, bd=0, highlightthickness=0)
        card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                  padx=(0, 10) if not is_last else 0, ipadx=10, ipady=8)

        accent = tk.Frame(card, bg=accent_color, width=6)
        accent.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        content = tk.Frame(card, bg=bg_color)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        title_label = tk.Label(
            content,
            text=title,
            bg=bg_color,
            fg=accent_color,
            font=("Microsoft YaHei", 10, "bold"),
            anchor="w"
        )
        title_label.pack(anchor="w")

        value_label = tk.Label(
            content,
            textvariable=value_var,
            bg=bg_color,
            fg="#0f172a",
            font=("Microsoft YaHei", 16, "bold"),
            anchor="w"
        )
        value_label.pack(anchor="w", pady=(6, 2))

        detail_label = tk.Label(
            content,
            textvariable=detail_var,
            bg=bg_color,
            fg="#475569",
            font=("Microsoft YaHei", 9),
            anchor="w"
        )
        detail_label.pack(anchor="w")

        return {
            "frame": card,
            "accent": accent,
            "content": content,
            "title": title_label,
            "value": value_label,
            "detail": detail_label
        }

    def style_status_card(self, card_key, bg_color, accent_color):
        card = self.status_cards.get(card_key)
        if not card:
            return

        card["frame"].configure(bg=bg_color)
        card["accent"].configure(bg=accent_color)
        card["content"].configure(bg=bg_color)
        card["title"].configure(bg=bg_color, fg=accent_color)
        card["value"].configure(bg=bg_color)
        card["detail"].configure(bg=bg_color)

    # ------------------- 阈值报警检查 -------------------
    def check_thresholds(self, depth, temp, sal, do, ph):
        """
        根据 THRESHOLDS 检查当前值是否越界。
        返回报警标记明细和中文报警字符串；若无报警则返回 "无报警"。
        """
        warnings = []
        alarm_flags = {
            "depth": False,
            "temp": False,
            "sal": False,
            "do": False,
            "ph": False,
        }

        low, high = THRESHOLDS["depth"]
        if pd.notna(depth) and not (low <= depth <= high):
            warnings.append("深度异常")
            alarm_flags["depth"] = True

        low, high = THRESHOLDS["temp"]
        if pd.notna(temp) and not (low <= temp <= high):
            warnings.append("温度异常")
            alarm_flags["temp"] = True

        low, high = THRESHOLDS["sal"]
        if pd.notna(sal) and not (low <= sal <= high):
            warnings.append("盐度异常")
            alarm_flags["sal"] = True
        low, high = THRESHOLDS["do"]
        if pd.notna(do) and not (low <= do <= high):
            warnings.append("含氧量异常")
            alarm_flags["do"] = True

        low, high = THRESHOLDS["ph"]
        if pd.notna(ph) and not (low <= ph <= high):
            if ph < low:
                warnings.append("pH偏低")
            elif ph > high:
                warnings.append("pH偏高")
            alarm_flags["ph"] = True

        if not warnings:
            return alarm_flags, "无报警"
        return alarm_flags, "报警：" + "，".join(warnings)

    def append_log(self, message, is_alarm=False):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_entries.append({
            "timestamp": timestamp,
            "message": message,
            "is_alarm": is_alarm
        })

        if self.log_text is None:
            return

        self.render_logs()

    def render_logs(self):
        if self.log_text is None:
            return

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)

        for entry in self.log_entries:
            if self.hide_normal_logs and not entry["is_alarm"]:
                continue
            self.log_text.insert(tk.END, f"[{entry['timestamp']}] ", ("timestamp",))
            tag_name = "alarm" if entry["is_alarm"] else "normal"
            self.log_text.insert(tk.END, f"{entry['message']}\n", (tag_name,))

        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def toggle_log_filter(self):
        self.hide_normal_logs = not self.hide_normal_logs
        if self.hide_normal_logs:
            self.log_toggle_text.set("显示全部日志")
        else:
            self.log_toggle_text.set("隐藏无报警信息")
        self.render_logs()

    def get_windowed_series(self, series):
        if len(series) > WINDOW_SIZE:
            return series[-WINDOW_SIZE:]
        return series

    def mark_alarm_points(self, ax, xs, ys, flags):
        alarm_xs = [x for x, flag in zip(xs, flags) if flag]
        alarm_ys = [y for y, flag in zip(ys, flags) if flag]
        if alarm_xs:
            ax.scatter(
                alarm_xs,
                alarm_ys,
                s=130,
                marker="*",
                color="#ef4444",
                edgecolors="#7f1d1d",
                linewidths=0.8,
                zorder=5,
                label="报警点"
            )

    def style_axis(self, ax, title, ylabel, xlabel=None):
        ax.set_title(title, fontsize=11, fontweight="bold", color="#1f2937")
        ax.set_ylabel(ylabel)
        if xlabel:
            ax.set_xlabel(xlabel)
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.set_facecolor("#ffffff")
        for spine in ax.spines.values():
            spine.set_color("#cbd5e1")
        handles, labels = ax.get_legend_handles_labels()
        if handles and labels:
            ax.legend(loc="best", fontsize=8, frameon=False)

    # ------------------- 数据展示 & 绘图 -------------------
    def update_display(self):
        n = len(self.df)
        if n == 0:
            return

        if self.current_idx < 0:
            self.current_idx = 0
        if self.current_idx >= n:
            self.current_idx = n - 1

        row = self.df.iloc[self.current_idx]

        self.var_depth.set(str(row[COL_DEPTH]))
        self.var_temp.set(str(row[COL_TEMP]))
        self.var_sal.set(str(row[COL_SAL]))
        self.var_do.set(str(row[COL_DO]))
        self.var_ph.set(str(row[COL_PH]))
        self.var_ql.set(str(row[COL_QL]))

        current_id = row[ID_COL]
        update_time = datetime.now().strftime("%H:%M:%S")
        self.label_record.config(
            text=f"当前记录: {self.current_idx + 1} / {n} (id={current_id})"
        )

        # --- 状态栏 + 阈值报警 ---
        self.var_status1.set("读取成功")
        self.var_status1_detail.set(f"记录 {self.current_idx + 1}/{n} 已载入，ID = {current_id}")
        self.var_status_time.set(update_time)
        self.var_status_time_detail.set(f"已同步最新数据点，来源记录 ID = {current_id}")
        self.style_status_card("read", "#eff6ff", "#2563eb")
        self.style_status_card("time", "#f8fafc", "#64748b")

        # 提取用于报警检查的数值（注意 NaN 处理）
        depth  = row[COL_DEPTH]
        temp   = row[COL_TEMP]
        sal    = row[COL_SAL]
        do     = row[COL_DO]
        ph     = row[COL_PH]

        alarm_flags, alarm_msg = self.check_thresholds(depth, temp, sal, do, ph)

        if self.is_auto_running:
            mode = "自动模式（每1秒）"
        else:
            mode = "手动模式"

        # 状态2：模式 + 报警信息
        self.var_status2.set(mode)
        self.var_status2_detail.set(alarm_msg)
        if alarm_msg == "无报警":
            self.style_status_card("run", "#f0fdf4", "#16a34a")
        else:
            self.style_status_card("run", "#fef2f2", "#dc2626")

        # 如果当前 id 还没画过，就追加到趋势数据中
        if current_id not in self.x_ids:
            self.x_ids.append(current_id)
            self.depth_values.append(row[COL_DEPTH])
            self.temp_values.append(row[COL_TEMP])
            self.sal_values.append(row[COL_SAL])
            self.do_values.append(row[COL_DO])
            self.ph_values.append(row[COL_PH])
            self.ql_values.append(row[COL_QL])
            for key in ["depth", "temp", "sal", "do", "ph"]:
                self.alarm_flags[key].append(alarm_flags[key])
            self.alarm_flags["any"].append(any(alarm_flags.values()))

            self.redraw_plots()

        log_message = (
            f"读取记录 id={current_id}，模式：{mode}，"
            f"Depth={depth}，Temp={temp}，Sal={sal}，DO={do}，pH={ph}，结果：{alarm_msg}"
        )
        self.append_log(log_message, is_alarm=alarm_msg != "无报警")

    def redraw_plots(self):
        if self.fig is None or self.ax_depth is None:
            return

        xs = self.get_windowed_series(self.x_ids)
        ys_d = self.get_windowed_series(self.depth_values)
        ys_t = self.get_windowed_series(self.temp_values)
        ys_s = self.get_windowed_series(self.sal_values)
        ys_o = self.get_windowed_series(self.do_values)
        ys_p = self.get_windowed_series(self.ph_values)
        ys_q = self.get_windowed_series(self.ql_values)
        flags_depth = self.get_windowed_series(self.alarm_flags["depth"])
        flags_temp = self.get_windowed_series(self.alarm_flags["temp"])
        flags_sal = self.get_windowed_series(self.alarm_flags["sal"])
        flags_do = self.get_windowed_series(self.alarm_flags["do"])
        flags_ph = self.get_windowed_series(self.alarm_flags["ph"])
        flags_any = self.get_windowed_series(self.alarm_flags["any"])

        # 清空旧图
        self.ax_depth.cla()
        self.ax_temp.cla()
        self.ax_sal.cla()
        self.ax_do.cla()
        self.ax_ph.cla()
        self.ax_ql.cla()

        # 画新图（折线 + 点）
        self.ax_depth.plot(xs, ys_d, marker="o", linestyle="-", color="#2563eb", linewidth=2)
        self.ax_temp.plot(xs, ys_t, marker="o", linestyle="-", color="#ef4444", linewidth=2)
        self.ax_sal.plot(xs, ys_s, marker="o", linestyle="-", color="#10b981", linewidth=2)
        self.ax_do.plot(xs, ys_o, marker="o", linestyle="-", color="#a855f7", linewidth=2)
        self.ax_ph.plot(xs, ys_p, marker="o", linestyle="-", color="#06b6d4", linewidth=2)
        # 水质等级是 0/1/2/3，可以用阶梯/离散线，同样用折线+点即可
        self.ax_ql.plot(xs, ys_q, marker="o", linestyle="-", color="#f59e0b", linewidth=2)

        self.mark_alarm_points(self.ax_depth, xs, ys_d, flags_depth)
        self.mark_alarm_points(self.ax_temp, xs, ys_t, flags_temp)
        self.mark_alarm_points(self.ax_sal, xs, ys_s, flags_sal)
        self.mark_alarm_points(self.ax_do, xs, ys_o, flags_do)
        self.mark_alarm_points(self.ax_ph, xs, ys_p, flags_ph)
        self.mark_alarm_points(self.ax_ql, xs, ys_q, flags_any)

        # 设置坐标轴标签
        self.style_axis(self.ax_depth, "深度趋势", "Depth(m)")
        self.style_axis(self.ax_temp, "温度趋势", "Temperature(°C)")
        self.style_axis(self.ax_sal, "盐度趋势", "Salinity")
        self.style_axis(self.ax_do, "含氧量趋势", "DO(mg/L)")
        self.style_axis(self.ax_ph, "pH 趋势", "pH")
        self.style_axis(self.ax_ql, "水质等级趋势", "Water Quality Level")
        self.ax_ql.set_yticks([0, 1, 2, 3])

        # 调整布局，防止纵轴标题被裁掉
        self.fig.subplots_adjust(left=0.09, right=0.98,
                                top=0.96, bottom=0.06,
                                wspace=0.30, hspace=0.35)

        self.canvas.draw()

    # ------------------- 手动上一条 / 下一条 -------------------
    def prev_record(self):
        if self.current_idx > 0:
            self.current_idx -= 1
            self.update_display()
        else:
            self.var_status2.set("手动模式")
            self.var_status2_detail.set("已经是第一条记录")
            self.style_status_card("run", "#fff7ed", "#ea580c")

    def next_record(self):
        if self.current_idx < len(self.df) - 1:
            self.current_idx += 1
            self.update_display()
        else:
            self.var_status2.set("手动模式")
            self.var_status2_detail.set("已经是最后一条记录")
            self.style_status_card("run", "#fff7ed", "#ea580c")
            if self.is_auto_running:
                self.stop_auto()

    # ------------------- 自动每 1 秒读取 -------------------
    def start_auto(self):
        if self.is_auto_running:
            return
        self.is_auto_running = True
        self.var_status2.set("自动模式（每1秒）")
        self.var_status2_detail.set("系统将按固定间隔自动读取下一条记录")
        self.style_status_card("run", "#ecfeff", "#0891b2")
        self.schedule_next_auto()

    def stop_auto(self):
        self.is_auto_running = False
        self.var_status2.set("自动模式已停止")
        self.var_status2_detail.set("自动读取已暂停，可继续手动浏览")
        self.style_status_card("run", "#f8fafc", "#64748b")
        if self.auto_job_id is not None:
            self.root.after_cancel(self.auto_job_id)
            self.auto_job_id = None

    def schedule_next_auto(self):
        if not self.is_auto_running:
            return
        self.auto_job_id = self.root.after(AUTO_INTERVAL_MS, self.auto_step)

    def auto_step(self):
        if self.current_idx < len(self.df) - 1:
            self.current_idx += 1
            self.update_display()
            self.schedule_next_auto()
        else:
            self.var_status2.set("自动读取结束")
            self.var_status2_detail.set("已到达最后一条记录")
            self.style_status_card("run", "#fff7ed", "#ea580c")
            self.is_auto_running = False
            self.auto_job_id = None


def main():
    root = tk.Tk()
    app = DataVisualizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()