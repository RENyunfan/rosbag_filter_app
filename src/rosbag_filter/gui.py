from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Literal, Optional

from .core import FilterCancelled, filter_bag, inspect_bag, resolve_time_window_ns
from .models import BagInfo, FilterProgressEvent, FilterResult, FilterSpec, PointCloudBoundsFilter


def _seconds_from_ns(value_ns: int) -> float:
    return value_ns / 1e9


class CollapsiblePanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        *,
        expanded: bool = False,
        on_toggle: Optional[Callable[[bool], None]] = None,
    ):
        super().__init__(parent)
        self._expanded = expanded
        self._title = title
        self._on_toggle = on_toggle
        self.toggle_button = ttk.Button(self, command=self.toggle, style="TButton")
        self.toggle_button.grid(row=0, column=0, sticky="w")
        self.body = ttk.Frame(self)
        self.body.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.columnconfigure(0, weight=1)
        self.body.columnconfigure(0, weight=1)
        self._sync()

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._sync()
        if self._on_toggle is not None:
            self._on_toggle(self._expanded)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _sync(self) -> None:
        symbol = "v" if self._expanded else ">"
        self.toggle_button.configure(text=f"{symbol} {self._title}")
        if self._expanded:
            self.body.grid()
        else:
            self.body.grid_remove()


PathDialogMode = Literal["open_any", "save_bag", "save_ros2"]


class HiddenPathDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        *,
        title: str,
        mode: PathDialogMode,
        initial: Optional[Path] = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(True, True)
        self.result: Optional[str] = None
        self.mode = mode
        self._entries: dict[str, Path] = {}

        self._bg = "#f5f5f7"
        self._card = "#ffffff"
        self._text = "#1d1d1f"
        self._muted = "#6e6e73"
        self._accent = "#0a84ff"
        self._border = "#d2d2d7"
        self._ros1_fg = "#8a5a00"
        self._ros1_bg = "#fff7df"
        self._ros2_fg = "#0f6b3a"
        self._ros2_bg = "#edf8f1"

        initial_path = Path(initial).expanduser() if initial else Path.cwd()
        if initial_path.is_file():
            self.current_dir = initial_path.parent
            initial_name = initial_path.name
        else:
            self.current_dir = initial_path if initial_path.exists() else Path.cwd()
            initial_name = ""

        self.current_dir = self.current_dir.resolve()
        self.path_var = tk.StringVar(value=str(self.current_dir))
        self.name_var = tk.StringVar(value=initial_name)
        self._tree_font = tkfont.nametofont("TkDefaultFont").copy()
        self._tree_font.configure(size=10)
        self._tree_heading_font = tkfont.nametofont("TkDefaultFont").copy()
        self._tree_heading_font.configure(size=10, weight="bold")

        self.configure(bg=self._bg)
        self.geometry("820x540")
        self.minsize(720, 430)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        style = ttk.Style(self)
        style.configure(
            "Browser.Treeview",
            background=self._card,
            fieldbackground=self._card,
            foreground=self._text,
            font=self._tree_font,
            rowheight=max(26, self._tree_font.metrics("linespace") + 10),
            bordercolor=self._border,
        )
        style.configure("Browser.Treeview.Heading", font=self._tree_heading_font)
        style.map(
            "Browser.Treeview",
            background=[("selected", self._accent)],
            foreground=[("selected", "#ffffff")],
        )

        header = ttk.Frame(self, padding=(16, 16, 16, 4))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=title, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=self._help_text(), style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        top = ttk.Frame(self, padding=(16, 8, 16, 8))
        top.grid(row=1, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Button(top, text="Up", command=self._go_up, width=9).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(top, textvariable=self.path_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(top, text="Go", command=self._go_to_typed_path, width=9).grid(row=0, column=2, padx=(8, 0))

        columns = ttk.Frame(self, padding=(16, 0, 16, 4))
        columns.grid(row=2, column=0, sticky="ew")
        columns.columnconfigure(0, weight=1)
        ttk.Label(
            columns,
            text="ROS1 .bag files and ROS2 bag folders are highlighted.",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")

        list_frame = ttk.Frame(self, padding=(16, 0, 16, 8))
        list_frame.grid(row=3, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            list_frame,
            columns=("kind",),
            show="tree headings",
            selectmode="browse",
            style="Browser.Treeview",
        )
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("kind", text="Type", anchor="e")
        self.tree.column("#0", width=560, minwidth=260, stretch=True)
        self.tree.column("kind", width=120, minwidth=100, stretch=False, anchor="e")
        self.tree.tag_configure("ros1", foreground=self._ros1_fg, background=self._ros1_bg)
        self.tree.tag_configure("ros2", foreground=self._ros2_fg, background=self._ros2_bg)
        self.tree.tag_configure("folder", foreground=self._text, background=self._card)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-Button-1>", lambda _event: self._open_or_select())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_name_from_selection())

        bottom = ttk.Frame(self, padding=(16, 8, 16, 16))
        bottom.grid(row=4, column=0, sticky="ew")
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text=self._name_label()).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(bottom, textvariable=self.name_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(bottom, text="Cancel", command=self._cancel, width=12).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(bottom, text=self._select_label(), command=self._select, width=14, style="Accent.TButton").grid(
            row=0, column=3, padx=(8, 0)
        )

        self.bind("<Escape>", lambda _event: self._cancel())
        self.bind("<Return>", lambda _event: self._select())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._refresh()
        self.grab_set()
        self.wait_window()

    def _help_text(self) -> str:
        if self.mode == "open_any":
            return "Showing visible folders, .bag files, and ROS2 bag folders. Hidden dot-folders are omitted."
        if self.mode == "save_bag":
            return "Choose a visible folder and enter a .bag filename."
        return "Choose or enter the output ROS2 bag folder name. Hidden dot-folders are omitted."

    def _name_label(self) -> str:
        if self.mode in {"open_any", "save_bag"}:
            return "File"
        return "Folder"

    def _select_label(self) -> str:
        return "Save" if self.mode in {"save_bag", "save_ros2"} else "Select"

    @staticmethod
    def _is_visible(path: Path) -> bool:
        return not path.name.startswith(".")

    @staticmethod
    def _is_ros2_bag(path: Path) -> bool:
        return path.is_dir() and (path / "metadata.yaml").is_file()

    def _refresh(self) -> None:
        self.path_var.set(str(self.current_dir))
        self.tree.delete(*self.tree.get_children(""))
        self._entries = {}
        try:
            children = sorted(self.current_dir.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError as exc:
            messagebox.showerror("Browse error", str(exc), parent=self)
            return

        for child in children:
            if not self._is_visible(child):
                continue
            if child.is_dir():
                kind = "ROS2 bag" if self._is_ros2_bag(child) else "Folder"
                tag = "ros2" if kind == "ROS2 bag" else "folder"
                self._insert_entry(child, kind, tag)
            elif self.mode in {"open_any", "save_bag"} and child.suffix == ".bag":
                self._insert_entry(child, "ROS1 .bag", "ros1")

    def _insert_entry(self, path: Path, kind: str, tag: str) -> None:
        item_id = f"entry-{len(self._entries)}"
        self._entries[item_id] = path
        self.tree.insert("", tk.END, iid=item_id, text=path.name, values=(kind,), tags=(tag,))

    def _selected_path(self) -> Optional[Path]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._entries.get(selection[0])

    def _sync_name_from_selection(self) -> None:
        selected = self._selected_path()
        if selected is None:
            return
        self.name_var.set(selected.name)

    def _open_or_select(self) -> None:
        selected = self._selected_path()
        if selected is None:
            return
        if selected.is_dir():
            if self.mode == "open_any" and self._is_ros2_bag(selected):
                self.result = str(selected)
                self.destroy()
                return
            self.current_dir = selected.resolve()
            self.name_var.set("")
            self._refresh()
            return
        self.result = str(selected)
        self.destroy()

    def _go_up(self) -> None:
        parent = self.current_dir.parent
        if parent != self.current_dir:
            self.current_dir = parent
            self.name_var.set("")
            self._refresh()

    def _go_to_typed_path(self) -> None:
        target = Path(self.path_var.get()).expanduser()
        if target.is_file():
            target = target.parent
        if not target.is_dir():
            messagebox.showerror("Browse error", "Path is not a folder.", parent=self)
            self.path_var.set(str(self.current_dir))
            return
        self.current_dir = target.resolve()
        self.name_var.set("")
        self._refresh()

    def _select(self) -> None:
        name = self.name_var.get().strip()
        selected = self._selected_path()

        if self.mode == "open_any":
            path = selected if selected is not None else self.current_dir / name
            if path.is_file() and path.suffix == ".bag":
                self.result = str(path)
            elif self._is_ros2_bag(path):
                self.result = str(path)
            else:
                messagebox.showerror("Select bag", "Select a visible ROS1 .bag file or ROS2 bag folder.", parent=self)
                return
        elif self.mode == "save_bag":
            if not name:
                messagebox.showerror("Save .bag", "Enter an output .bag filename.", parent=self)
                return
            path = self.current_dir / name
            if path.suffix != ".bag":
                path = path.with_suffix(".bag")
            self.result = str(path)
        else:
            if selected and selected.is_dir() and not name:
                path = selected
            elif name:
                path = self.current_dir / name
            else:
                path = self.current_dir
            self.result = str(path)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class RosbagFilterGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ROS Bag Filter")
        self._default_window_width = 1040
        self._default_window_height = 740
        self.root.geometry(f"{self._default_window_width}x{self._default_window_height}")
        self.root.minsize(920, 660)

        self._bag_info: Optional[BagInfo] = None
        self._worker: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._event_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._path_widgets: dict[str, tk.Label] = {}
        self._path_vars: dict[str, tk.StringVar] = {}
        self._path_display_vars: dict[str, tk.StringVar] = {}
        self._path_placeholders: dict[str, str] = {}

        self._apply_theme()
        self._build_widgets()
        self.root.after(100, self._poll_events)

    def _apply_theme(self) -> None:
        self.style = ttk.Style()
        try:
            import sv_ttk  # type: ignore

            sv_ttk.set_theme("light")
            self.root.configure(bg="#f5f5f7")
        except Exception:
            bg = "#f5f5f7"
            card = "#ffffff"
            text = "#1d1d1f"
            muted = "#6e6e73"
            accent = "#0a84ff"
            accent_hover = "#006edc"
            border = "#d2d2d7"
            self.root.configure(bg=bg)
            self.style.theme_use("clam")
            self.style.configure(".", background=bg, foreground=text)
            self.style.configure("TFrame", background=bg)
            self.style.configure("Card.TFrame", background=card)
            self.style.configure("TLabel", background=bg, foreground=text)
            self.style.configure("Muted.TLabel", background=bg, foreground=muted)
            self.style.configure("Title.TLabel", background=bg, foreground=text, font=("TkDefaultFont", 13, "bold"))
            self.style.configure("TLabelframe", background=bg, foreground=text, bordercolor=border)
            self.style.configure("TLabelframe.Label", background=bg, foreground=muted)
            self.style.configure(
                "TEntry",
                fieldbackground=card,
                bordercolor=border,
                lightcolor=border,
                darkcolor=border,
                padding=5,
            )
            self.style.configure(
                "TButton",
                background=card,
                bordercolor=border,
                padding=(10, 6),
            )
            self.style.map("TButton", background=[("active", "#ececf0")])
            self.style.configure(
                "Accent.TButton",
                background=accent,
                foreground="#ffffff",
                bordercolor=accent,
                padding=(12, 6),
            )
            self.style.map("Accent.TButton", background=[("active", accent_hover)])
            self.style.configure(
                "TProgressbar",
                background=accent,
                troughcolor="#e5e5ea",
                bordercolor=bg,
                lightcolor=accent,
                darkcolor=accent,
                thickness=8,
            )

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="Unified ROS Bag Filter", style="Title.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10)
        )
        ttk.Label(
            outer,
            text="ROS1 .bag and ROS2 rosbag2 filtering from one UI.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 12))

        self.input_var = tk.StringVar()
        self.input_display_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.output_display_var = tk.StringVar()
        self.time_mode_var = tk.StringVar(value="ros")
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.workers_var = tk.StringVar(value=str(max(1, (os.cpu_count() or 2) - 1)))
        self.cloud_enabled_var = tk.BooleanVar(value=False)
        self.cloud_topic_var = tk.StringVar(value="/cloud_registered")

        self.cloud_bounds_vars = {
            "x_min": tk.StringVar(),
            "x_max": tk.StringVar(),
            "y_min": tk.StringVar(),
            "y_max": tk.StringVar(),
            "z_min": tk.StringVar(),
            "z_max": tk.StringVar(),
        }

        self._build_path_row(
            outer,
            row=2,
            key="input",
            label="Input bag",
            variable=self.input_var,
            display_variable=self.input_display_var,
            placeholder="Select a ROS1 file or ROS2 directory...",
            action_text="Load",
            action_command=self._browse_input,
        )
        self._build_path_row(
            outer,
            row=3,
            key="output",
            label="Output bag",
            variable=self.output_var,
            display_variable=self.output_display_var,
            placeholder="Choose where to write the filtered bag...",
            action_text="Save",
            action_command=self._browse_output,
        )

        time_frame = ttk.Labelframe(outer, text="Time window", padding=10)
        time_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(12, 6))
        for column in range(4):
            time_frame.columnconfigure(column, weight=1 if column in (1, 3) else 0)
        ttk.Radiobutton(
            time_frame,
            text="ROS time (s)",
            value="ros",
            variable=self.time_mode_var,
            command=self._refresh_time_fields,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            time_frame,
            text="Relative (s)",
            value="relative",
            variable=self.time_mode_var,
            command=self._refresh_time_fields,
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(time_frame, text="Start").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(time_frame, textvariable=self.start_var).grid(row=1, column=1, sticky="ew", pady=(8, 0), padx=(0, 12))
        ttk.Label(time_frame, text="End").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(time_frame, textvariable=self.end_var).grid(row=1, column=3, sticky="ew", pady=(8, 0))

        topics_frame = ttk.Labelframe(outer, text="Topics to keep", padding=10)
        topics_frame.grid(row=5, column=0, columnspan=4, sticky="nsew", pady=6)
        topics_frame.columnconfigure(0, weight=1)
        topics_frame.rowconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)
        self.topics_listbox = tk.Listbox(
            topics_frame,
            selectmode=tk.MULTIPLE,
            activestyle="none",
            exportselection=False,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d2d2d7",
            highlightcolor="#0a84ff",
            bg="#ffffff",
            fg="#1d1d1f",
            selectbackground="#0a84ff",
            selectforeground="#ffffff",
        )
        self.topics_listbox.grid(row=0, column=0, sticky="nsew")
        topics_scroll = ttk.Scrollbar(topics_frame, orient="vertical", command=self.topics_listbox.yview)
        topics_scroll.grid(row=0, column=1, sticky="ns")
        self.topics_listbox.configure(yscrollcommand=topics_scroll.set)
        topic_buttons = ttk.Frame(topics_frame)
        topic_buttons.grid(row=0, column=2, sticky="n", padx=(10, 0))
        ttk.Button(topic_buttons, text="Select all", command=self._select_all_topics).pack(fill="x", pady=(0, 6))
        ttk.Button(topic_buttons, text="Clear", command=self._clear_topics).pack(fill="x")

        options_frame = ttk.Frame(outer)
        options_frame.grid(row=6, column=0, columnspan=4, sticky="ew", pady=6)
        options_frame.columnconfigure(1, weight=1)
        ttk.Label(options_frame, text="Workers").grid(row=0, column=0, sticky="w")
        ttk.Entry(options_frame, textvariable=self.workers_var, width=8).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            options_frame,
            text="Open the advanced panel only when you need cloud bounds.",
            style="Muted.TLabel",
        ).grid(row=0, column=2, sticky="e", padx=(18, 0))

        self.cloud_panel = CollapsiblePanel(
            outer,
            "Advanced PointCloud2 Filter",
            expanded=False,
            on_toggle=self._handle_cloud_panel_toggle,
        )
        self.cloud_panel.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(4, 6))
        self._build_cloud_panel(self.cloud_panel.body)

        self.progress_bar = ttk.Progressbar(outer, mode="determinate")
        self.progress_bar.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(8, 4))

        self.log_widget = tk.Text(
            outer,
            height=8,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d2d2d7",
            highlightcolor="#d2d2d7",
            bg="#ffffff",
            fg="#1d1d1f",
            relief="flat",
            padx=10,
            pady=8,
            state=tk.DISABLED,
        )
        self.log_widget.grid(row=9, column=0, columnspan=4, sticky="nsew", pady=(6, 8))
        outer.rowconfigure(9, weight=1)

        button_row = ttk.Frame(outer)
        button_row.grid(row=10, column=0, columnspan=4, sticky="e")
        self.cancel_button = ttk.Button(button_row, text="Cancel", command=self._cancel, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.run_button = ttk.Button(button_row, text="Filter", style="Accent.TButton", command=self._start_filter)
        self.run_button.pack(side=tk.RIGHT)

        self.cloud_enabled_var.trace_add("write", lambda *_: self._sync_cloud_fields())
        self._sync_cloud_fields()

    def _build_path_row(
        self,
        parent: ttk.Frame,
        *,
        row: int,
        key: str,
        label: str,
        variable: tk.StringVar,
        display_variable: tk.StringVar,
        placeholder: str,
        action_text: str,
        action_command: Callable[[], None],
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        path_frame = ttk.Frame(parent)
        path_frame.grid(row=row, column=1, sticky="ew", pady=4)
        path_frame.columnconfigure(0, weight=1)
        path_label = tk.Label(
            path_frame,
            textvariable=display_variable,
            anchor="w",
            bg="#ffffff",
            fg="#1d1d1f",
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#d2d2d7",
            highlightcolor="#d2d2d7",
            padx=10,
            pady=8,
        )
        path_label.grid(row=0, column=0, sticky="ew")
        self._path_widgets[key] = path_label
        self._path_vars[key] = variable
        self._path_display_vars[key] = display_variable
        self._path_placeholders[key] = placeholder
        path_label.bind("<Configure>", lambda _event, name=key: self._update_path_display(name))
        variable.trace_add("write", lambda *_args, name=key: self._update_path_display(name))
        button_frame = ttk.Frame(parent)
        button_frame.grid(row=row, column=2, columnspan=2, sticky="e", padx=(10, 0), pady=4)
        ttk.Button(button_frame, text=action_text, command=action_command, width=14).pack(side=tk.LEFT)
        self._update_path_display(key)

    def _update_path_display(self, key: str) -> None:
        widget = self._path_widgets[key]
        value = self._path_vars[key].get().strip()
        if not value:
            self._path_display_vars[key].set(self._path_placeholders[key])
            return

        available_width = max(widget.winfo_width() - 18, 120)
        font = tkfont.nametofont(widget.cget("font"))
        self._path_display_vars[key].set(self._ellipsize_middle(value, available_width, font))

    @staticmethod
    def _ellipsize_middle(value: str, max_width_px: int, font: tkfont.Font) -> str:
        if font.measure(value) <= max_width_px:
            return value

        ellipsis = "..."
        left = max(8, len(value) // 3)
        right = max(12, len(value) // 3)
        while left > 1 or right > 1:
            candidate = f"{value[:left]}{ellipsis}{value[-right:]}"
            if font.measure(candidate) <= max_width_px:
                return candidate
            if right >= left and right > 1:
                right -= 1
            elif left > 1:
                left -= 1
            else:
                break
        return ellipsis

    def _build_cloud_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(2, weight=1)
        ttk.Checkbutton(
            parent,
            text="Enable PointCloud2 filter",
            variable=self.cloud_enabled_var,
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        ttk.Label(parent, text="Topic").grid(row=1, column=0, sticky="w")
        self.cloud_topic_entry = ttk.Entry(parent, textvariable=self.cloud_topic_var)
        self.cloud_topic_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(0, 10))

        headers = ("Axis", "Min", "Max")
        for index, header in enumerate(headers):
            ttk.Label(parent, text=header).grid(row=2, column=index, sticky="w", padx=(0, 8))
        for row, axis in enumerate(("x", "y", "z"), start=3):
            ttk.Label(parent, text=axis.upper()).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(parent, textvariable=self.cloud_bounds_vars[f"{axis}_min"], width=12).grid(
                row=row, column=1, sticky="w", pady=4
            )
            ttk.Entry(parent, textvariable=self.cloud_bounds_vars[f"{axis}_max"], width=12).grid(
                row=row, column=2, sticky="w", pady=4
            )

    def _handle_cloud_panel_toggle(self, expanded: bool) -> None:
        self._sync_cloud_fields()
        self.root.after_idle(lambda: self._fit_window_to_content(shrink=not expanded))

    def _fit_window_to_content(self, *, shrink: bool) -> None:
        self.root.update_idletasks()
        current_width = max(self.root.winfo_width(), self._default_window_width)
        current_height = max(self.root.winfo_height(), self._default_window_height)
        requested_height = self.root.winfo_reqheight()
        screen_limit = self.root.winfo_screenheight() - 80

        if shrink:
            target_height = max(self._default_window_height, min(current_height, requested_height))
        else:
            target_height = max(current_height, requested_height)

        target_height = min(target_height, screen_limit)
        self.root.geometry(f"{current_width}x{target_height}")

    def _log(self, message: str) -> None:
        self.log_widget.configure(state=tk.NORMAL)
        self.log_widget.insert(tk.END, message + "\n")
        self.log_widget.see(tk.END)
        self.log_widget.configure(state=tk.DISABLED)

    def _select_all_topics(self) -> None:
        self.topics_listbox.select_set(0, tk.END)

    def _clear_topics(self) -> None:
        self.topics_listbox.selection_clear(0, tk.END)

    def _initial_dialog_path(self, raw_path: str) -> Path:
        if raw_path.strip():
            path = Path(raw_path).expanduser()
            if path.exists():
                return path
            if path.parent.exists():
                return path.parent
        return Path.cwd()

    def _open_hidden_path_dialog(
        self,
        *,
        title: str,
        mode: PathDialogMode,
        initial: Path,
    ) -> str:
        dialog = HiddenPathDialog(self.root, title=title, mode=mode, initial=initial)
        return dialog.result or ""

    def _browse_input(self) -> None:
        path = self._open_hidden_path_dialog(
            title="Load ROS bag",
            mode="open_any",
            initial=self._initial_dialog_path(self.input_var.get()),
        )
        if path:
            self.input_var.set(path)
            self._load_input_bag()

    def _browse_output(self) -> None:
        mode: PathDialogMode = "save_ros2"
        if self._bag_info is not None and self._bag_info.format == "ros1":
            mode = "save_bag"
        elif self.output_var.get().strip().endswith(".bag"):
            mode = "save_bag"

        path = self._open_hidden_path_dialog(
            title="Save filtered bag",
            mode=mode,
            initial=self._initial_dialog_path(self.output_var.get()),
        )
        if path:
            self.output_var.set(path)

    def _load_input_bag(self) -> None:
        raw_path = self.input_var.get().strip()
        if not raw_path:
            return
        try:
            bag_info = inspect_bag(Path(raw_path))
        except Exception as exc:
            messagebox.showerror("Load error", str(exc))
            return

        self._bag_info = bag_info
        self.topics_listbox.delete(0, tk.END)
        for topic in bag_info.topics:
            self.topics_listbox.insert(tk.END, topic.name)
        self._select_all_topics()
        self._refresh_time_fields()
        if bag_info.format == "ros1":
            default_output = bag_info.path.with_name(f"filtered_{bag_info.path.name}")
        else:
            default_output = bag_info.path.parent / f"{bag_info.path.name}_filtered"
        self.output_var.set(str(default_output))
        self._log(
            f"Loaded {bag_info.format.upper()} bag with "
            f"{len(bag_info.topics)} topic(s) and {bag_info.message_count} message(s)."
        )

    def _refresh_time_fields(self) -> None:
        if self._bag_info is None:
            return
        if self.time_mode_var.get() == "ros":
            self.start_var.set(f"{_seconds_from_ns(self._bag_info.start_time_ns):.6f}")
            self.end_var.set(f"{_seconds_from_ns(self._bag_info.end_time_ns):.6f}")
        else:
            self.start_var.set("0.000000")
            self.end_var.set(f"{_seconds_from_ns(self._bag_info.duration_ns):.6f}")

    def _sync_cloud_fields(self) -> None:
        state = tk.NORMAL if self.cloud_enabled_var.get() else tk.DISABLED
        self.cloud_topic_entry.configure(state=state)
        for child in self.cloud_panel.body.winfo_children():
            if isinstance(child, ttk.Entry) and child is not self.cloud_topic_entry:
                child.configure(state=state)

    def _build_spec(self) -> FilterSpec:
        if self._bag_info is None:
            raise ValueError("Load an input bag first.")
        selected_topics = tuple(
            self.topics_listbox.get(index) for index in self.topics_listbox.curselection()
        )
        workers = max(1, int(self.workers_var.get()))
        start_ns, end_ns = resolve_time_window_ns(
            self._bag_info,
            time_mode=self.time_mode_var.get(),
            start=float(self.start_var.get()),
            end=float(self.end_var.get()),
        )
        cloud_filter = None
        if self.cloud_enabled_var.get():
            cloud_filter = PointCloudBoundsFilter(
                topic=self.cloud_topic_var.get().strip(),
                x_min=self._parse_optional_float(self.cloud_bounds_vars["x_min"].get()),
                x_max=self._parse_optional_float(self.cloud_bounds_vars["x_max"].get()),
                y_min=self._parse_optional_float(self.cloud_bounds_vars["y_min"].get()),
                y_max=self._parse_optional_float(self.cloud_bounds_vars["y_max"].get()),
                z_min=self._parse_optional_float(self.cloud_bounds_vars["z_min"].get()),
                z_max=self._parse_optional_float(self.cloud_bounds_vars["z_max"].get()),
            )
        return FilterSpec(
            input_path=Path(self.input_var.get().strip()).expanduser().resolve(),
            output_path=Path(self.output_var.get().strip()).expanduser().resolve(),
            topics=selected_topics,
            start_time_ns=start_ns,
            end_time_ns=end_ns,
            format=self._bag_info.format,
            workers=workers,
            pointcloud_filter=cloud_filter,
        )

    @staticmethod
    def _parse_optional_float(raw_value: str) -> Optional[float]:
        value = raw_value.strip()
        return None if not value else float(value)

    def _start_filter(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        try:
            spec = self._build_spec()
            spec.validate()
        except Exception as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return

        self._cancel_event = threading.Event()
        self.run_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.progress_bar.configure(maximum=1, value=0)
        self._log("Starting filter run.")

        def worker() -> None:
            try:
                result = filter_bag(spec, event_cb=self._push_event, cancel_event=self._cancel_event)
            except FilterCancelled as exc:
                self._event_queue.put(("cancelled", str(exc)))
            except Exception as exc:
                self._event_queue.put(("error", str(exc)))
            else:
                self._event_queue.put(("result", result))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _cancel(self) -> None:
        self._cancel_event.set()
        self._log("Cancel requested.")

    def _push_event(self, event: FilterProgressEvent) -> None:
        self._event_queue.put(("event", event))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self._event_queue.get_nowait()
                if kind == "event":
                    event = payload
                    if event.kind == "log":
                        self._log(event.message)
                    elif event.kind == "progress":
                        self.progress_bar.configure(maximum=max(event.total, 1), value=event.current)
                elif kind == "result":
                    result = payload
                    self._finish_run()
                    self._log(
                        f"Done in {result.duration_seconds:.2f}s. Output: {result.output_path}"
                    )
                    messagebox.showinfo("Filter complete", f"Filtered bag written to {result.output_path}")
                elif kind == "cancelled":
                    self._finish_run()
                    self._log(str(payload))
                    messagebox.showinfo("Cancelled", str(payload))
                elif kind == "error":
                    self._finish_run()
                    self._log(f"Error: {payload}")
                    messagebox.showerror("Filter error", str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _finish_run(self) -> None:
        self.run_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)


def main() -> int:
    root = tk.Tk()
    RosbagFilterGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
