"""Macro Bot GUI: build a sequence of clicks and key presses for Windows.

Steps can be added manually (coordinates + button, key name, delay). Click
captures a point: the next mouse click anywhere sets the X/Y fields. Only
single-key presses are supported (no modifier combos).

Global hotkey F12 stops playback immediately.
Run as Administrator if the game ignores synthetic input.
"""

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import cv2
import keyboard
import mouse
import numpy as np
from PIL import Image, ImageGrab

MACRO_FILE = Path(__file__).with_name("macro.json")
TEMPLATE_DIR = Path(__file__).with_name("templates")
KEY_PRESS_PAUSE = 0.03

BUTTON_NAMES = {"left": "ЛКМ", "right": "ПКМ", "middle": "СКМ"}
MODIFIER_KEYS = ("ctrl", "alt", "shift", "left ctrl", "right ctrl",
                 "left alt", "right alt", "left shift", "right shift",
                 "left windows", "right windows")


class MacroApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Macro Bot")
        self.geometry("520x560")
        self.minsize(460, 480)

        self.steps = []
        self.stop_event = threading.Event()
        self.playing = False
        self.recording = False
        self.recording_last_t = 0.0
        self.capture_active = False
        self.capture_key_active = False
        self.capture_bar_active = False
        self.record_pressed = {}
        TEMPLATE_DIR.mkdir(exist_ok=True)

        self.q = queue.Queue()
        self._build_ui()
        self._load_silent()

        keyboard.add_hotkey("f12", lambda: self.q.put(("stop", None)))
        keyboard.add_hotkey("f8", lambda: self.q.put(("record_toggle", None)))
        self.after(100, self._poll_queue)
        self.after(100, self._update_cursor_label)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        cursor_frame = ttk.LabelFrame(self, text="Текущие координаты курсора")
        cursor_frame.pack(fill="x", **pad)
        self.cursor_label = ttk.Label(cursor_frame, text="X: ---  Y: ---", font=("Consolas", 11))
        self.cursor_label.pack(anchor="w", padx=8, pady=4)
        ttk.Button(cursor_frame, text="Задать координаты по клику мыши",
                   command=self._arm_click_capture).pack(anchor="w", padx=8, pady=(0, 6))

        click_frame = ttk.LabelFrame(self, text="Клик по координатам")
        click_frame.pack(fill="x", **pad)
        ttk.Label(click_frame, text="X:").grid(row=0, column=0, padx=6, pady=6)
        self.x_entry = ttk.Entry(click_frame, width=8)
        self.x_entry.grid(row=0, column=1, padx=2)
        ttk.Label(click_frame, text="Y:").grid(row=0, column=2, padx=6)
        self.y_entry = ttk.Entry(click_frame, width=8)
        self.y_entry.grid(row=0, column=3, padx=2)
        ttk.Label(click_frame, text="Кнопка:").grid(row=0, column=4, padx=6)
        self.button_combo = ttk.Combobox(click_frame, width=8, state="readonly",
                                         values=list(BUTTON_NAMES))
        self.button_combo.current(0)
        self.button_combo.grid(row=0, column=5, padx=2)
        ttk.Label(click_frame, text="Раз:").grid(row=0, column=6, padx=6)
        self.click_times_entry = ttk.Entry(click_frame, width=5)
        self.click_times_entry.insert(0, "1")
        self.click_times_entry.grid(row=0, column=7, padx=2)
        ttk.Label(click_frame, text="Интервал, сек:").grid(row=0, column=8, padx=6)
        self.click_interval_entry = ttk.Entry(click_frame, width=5)
        self.click_interval_entry.insert(0, "0.3")
        self.click_interval_entry.grid(row=0, column=9, padx=2)
        ttk.Button(click_frame, text="Добавить клик", command=self._add_click).grid(row=0, column=10, padx=8)

        key_frame = ttk.LabelFrame(self, text="Нажатие клавиши")
        key_frame.pack(fill="x", **pad)
        ttk.Label(key_frame, text="Клавиша:").grid(row=0, column=0, padx=6, pady=6)
        self.key_entry = ttk.Entry(key_frame, width=12)
        self.key_entry.grid(row=0, column=1, padx=2)
        ttk.Button(key_frame, text="Снять клавишу", command=self._capture_key).grid(row=0, column=2, padx=6)
        ttk.Label(key_frame, text="например: e, space, f").grid(row=0, column=3, padx=6)
        ttk.Label(key_frame, text="Раз:").grid(row=0, column=4, padx=6)
        self.key_times_entry = ttk.Entry(key_frame, width=5)
        self.key_times_entry.insert(0, "1")
        self.key_times_entry.grid(row=0, column=5, padx=2)
        ttk.Label(key_frame, text="Удерживать, сек:").grid(row=0, column=6, padx=6)
        self.key_hold_entry = ttk.Entry(key_frame, width=5)
        self.key_hold_entry.insert(0, "0.1")
        self.key_hold_entry.grid(row=0, column=7, padx=2)
        ttk.Button(key_frame, text="Добавить клавишу", command=self._add_key).grid(row=0, column=8, padx=8)

        walk_frame = ttk.LabelFrame(self, text="Ходьба (зажать клавиши на время)")
        walk_frame.pack(fill="x", **pad)
        ttk.Label(walk_frame, text="Клавиши (через +):", ).grid(row=0, column=0, padx=6, pady=6)
        self.walk_keys_entry = ttk.Entry(walk_frame, width=10)
        self.walk_keys_entry.insert(0, "w")
        self.walk_keys_entry.grid(row=0, column=1, padx=2)
        ttk.Label(walk_frame, text="Секунд:").grid(row=0, column=2, padx=6)
        self.walk_secs_entry = ttk.Entry(walk_frame, width=6)
        self.walk_secs_entry.insert(0, "3.0")
        self.walk_secs_entry.grid(row=0, column=3, padx=2)
        ttk.Button(walk_frame, text="Добавить ходьбу", command=self._add_walk).grid(row=0, column=4, padx=8)
        ttk.Label(walk_frame, text="например: w, w+a, s").grid(row=0, column=5, padx=8)

        hold_frame = ttk.LabelFrame(self, text="Удержание кнопки мыши (например, ПКМ — откручивать болты)")
        hold_frame.pack(fill="x", **pad)
        ttk.Label(hold_frame, text="X:").grid(row=0, column=0, padx=6, pady=6)
        self.hold_x_entry = ttk.Entry(hold_frame, width=8)
        self.hold_x_entry.grid(row=0, column=1, padx=2)
        ttk.Label(hold_frame, text="Y:").grid(row=0, column=2, padx=6)
        self.hold_y_entry = ttk.Entry(hold_frame, width=8)
        self.hold_y_entry.grid(row=0, column=3, padx=2)
        ttk.Label(hold_frame, text="Кнопка:").grid(row=0, column=4, padx=6)
        self.hold_button_combo = ttk.Combobox(hold_frame, width=8, state="readonly",
                                              values=list(BUTTON_NAMES))
        self.hold_button_combo.current(1)
        self.hold_button_combo.grid(row=0, column=5, padx=2)
        ttk.Label(hold_frame, text="Секунд:").grid(row=0, column=6, padx=6)
        self.hold_secs_entry = ttk.Entry(hold_frame, width=6)
        self.hold_secs_entry.insert(0, "3.0")
        self.hold_secs_entry.grid(row=0, column=7, padx=2)
        ttk.Button(hold_frame, text="Добавить удержание", command=self._add_hold_button).grid(row=0, column=8, padx=8)

        progress_frame = ttk.LabelFrame(self, text="Действие до заполнения шкалы (по цвету шкалы)")
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.columnconfigure(1, weight=1)
        progress_frame.columnconfigure(2, weight=1)
        progress_frame.pack(fill="x", **pad)
        click_col = ttk.Frame(progress_frame)
        click_col.grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(click_col, text="Где нажимать").pack(anchor="w")
        cx_row = ttk.Frame(click_col)
        cx_row.pack(anchor="w", pady=2)
        ttk.Label(cx_row, text="X:").pack(side="left")
        self.px_entry = ttk.Entry(cx_row, width=6)
        self.px_entry.pack(side="left", padx=2)
        ttk.Label(cx_row, text="Y:").pack(side="left", padx=(6, 0))
        self.py_entry = ttk.Entry(cx_row, width=6)
        self.py_entry.pack(side="left", padx=2)
        ttk.Label(click_col, text="Кнопка:").pack(anchor="w")
        self.p_button_combo = ttk.Combobox(click_col, width=8, state="readonly", values=list(BUTTON_NAMES))
        self.p_button_combo.current(0)
        self.p_button_combo.pack(anchor="w", pady=2)
        ttk.Label(click_col, text="Режим:").pack(anchor="w")
        self.p_mode_combo = ttk.Combobox(click_col, width=10, state="readonly", values=["Зажать", "Клики"])
        self.p_mode_combo.current(0)
        self.p_mode_combo.pack(anchor="w", pady=2)
        ttk.Label(click_col, text="Интервал кликов, сек:").pack(anchor="w")
        self.p_interval_entry = ttk.Entry(click_col, width=6)
        self.p_interval_entry.insert(0, "0.25")
        self.p_interval_entry.pack(anchor="w", pady=2)

        bar_col = ttk.Frame(progress_frame)
        bar_col.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(bar_col, text="Точка шкалы (верхнутая часть,\nкоторая красится при заполнении)").pack(anchor="w")
        bx_row = ttk.Frame(bar_col)
        bx_row.pack(anchor="w", pady=2)
        ttk.Label(bx_row, text="X:").pack(side="left")
        self.bar_x_entry = ttk.Entry(bx_row, width=6)
        self.bar_x_entry.pack(side="left", padx=2)
        ttk.Label(bx_row, text="Y:").pack(side="left", padx=(6, 0))
        self.bar_y_entry = ttk.Entry(bx_row, width=6)
        self.bar_y_entry.pack(side="left", padx=2)
        self.bar_color_label = ttk.Label(bar_col, text="Цвет: неизвестен", font=("Consolas", 9))
        self.bar_color_label.pack(anchor="w", pady=2)
        ttk.Button(bar_col, text="Снять цвет шкалы по клику",
                   command=self._arm_bar_capture).pack(anchor="w", pady=(4, 0))

        done_col = ttk.Frame(progress_frame)
        done_col.grid(row=0, column=2, sticky="nw", padx=8, pady=4)
        ttk.Label(done_col, text="Макс. секунд (страховка):").pack(anchor="w")
        self.p_timeout_entry = ttk.Entry(done_col, width=6)
        self.p_timeout_entry.insert(0, "20")
        self.p_timeout_entry.pack(anchor="w", pady=2)
        ttk.Button(done_col, text="Добавить шаг\n«до заполнения шкалы»", command=self._add_progress,
                   wraplength=170).pack(anchor="w", pady=6)

        find_frame = ttk.LabelFrame(self, text="Найти объект на экране по образцу (объекты, которые меняют место)")
        find_frame.pack(fill="x", **pad)
        ttk.Label(find_frame, text="Образец:").grid(row=0, column=0, padx=6, pady=6)
        self.tpl_combo = ttk.Combobox(find_frame, width=14, state="readonly")
        self.tpl_combo.grid(row=0, column=1, padx=2)
        ttk.Button(find_frame, text="Обновить", command=self._refresh_templates).grid(row=0, column=2, padx=4)
        ttk.Button(find_frame, text="Снять рамкой", command=self._capture_template).grid(row=0, column=3, padx=4)
        ttk.Label(find_frame, text="Действие:").grid(row=0, column=4, padx=6)
        self.find_action_combo = ttk.Combobox(find_frame, width=14, state="readonly",
                                              values=["Кликнуть N раз", "Удерживать",
                                                      "Ждать появления"])
        self.find_action_combo.current(0)
        self.find_action_combo.grid(row=0, column=5, padx=2)
        ttk.Label(find_frame, text="Раз:").grid(row=0, column=6, padx=6)
        self.find_times_entry = ttk.Entry(find_frame, width=5)
        self.find_times_entry.insert(0, "1")
        self.find_times_entry.grid(row=0, column=7, padx=2)
        ttk.Label(find_frame, text="Сек (интервал кликов / удержание):").grid(row=0, column=8, padx=6)
        self.find_param_entry = ttk.Entry(find_frame, width=6)
        self.find_param_entry.insert(0, "0.4")
        self.find_param_entry.grid(row=0, column=9, padx=2)
        ttk.Button(find_frame, text="Добавить «найти и нажать»",
                   command=self._add_find).grid(row=0, column=10, padx=8)
        self._refresh_templates()

        delay_frame = ttk.LabelFrame(self, text="Пауза между действиями")
        delay_frame.pack(fill="x", **pad)
        ttk.Label(delay_frame, text="Секунды:").grid(row=0, column=0, padx=6, pady=6)
        self.delay_entry = ttk.Entry(delay_frame, width=8)
        self.delay_entry.insert(0, "1.0")
        self.delay_entry.grid(row=0, column=1, padx=2)
        ttk.Button(delay_frame, text="Добавить паузу", command=self._add_delay).grid(row=0, column=2, padx=8)

        list_frame = ttk.LabelFrame(self, text="Сценарий (порядок сверху вниз)")
        list_frame.pack(fill="both", expand=True, **pad)
        self.listbox = tk.Listbox(list_frame, height=10, activestyle="dotbox")
        self.listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        scroll = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        scroll.pack(side="left", fill="y", pady=6)
        self.listbox.config(yscrollcommand=scroll.set)
        btn_col = ttk.Frame(list_frame)
        btn_col.pack(side="left", fill="y", padx=8, pady=6)
        ttk.Button(btn_col, text="↑ Выше", width=10,
                   command=lambda: self._move_step(-1)).pack(pady=2)
        ttk.Button(btn_col, text="↓ Ниже", width=10,
                   command=lambda: self._move_step(1)).pack(pady=2)
        ttk.Button(btn_col, text="Удалить", width=10, command=self._delete_step).pack(pady=2)
        ttk.Button(btn_col, text="Очистить всё", width=10, command=self._clear_all).pack(pady=2)

        play_frame = ttk.LabelFrame(self, text="Запуск")
        play_frame.pack(fill="x", **pad)
        ttk.Label(play_frame, text="Повторов:").grid(row=0, column=0, padx=6, pady=6)
        self.repeats_spin = ttk.Spinbox(play_frame, from_=1, to=999999, width=8)
        self.repeats_spin.set(1)
        self.repeats_spin.grid(row=0, column=1, padx=2)
        ttk.Label(play_frame, text="Скорость:").grid(row=0, column=2, padx=6)
        self.speed_spin = ttk.Spinbox(play_frame, from_=0.1, to=10, increment=0.1, width=6)
        self.speed_spin.set(1.0)
        self.speed_spin.grid(row=0, column=3, padx=2)
        self.play_btn = ttk.Button(play_frame, text="▶ Старт", command=self._start_playback)
        self.play_btn.grid(row=0, column=4, padx=8)
        self.stop_btn = ttk.Button(play_frame, text="■ Стоп", command=self._stop, state="disabled")
        self.stop_btn.grid(row=0, column=5, padx=2)
        self.rec_btn = ttk.Button(play_frame, text="● Запись", command=self._toggle_record)
        self.rec_btn.grid(row=0, column=6, padx=8)

        save_frame = ttk.Frame(self)
        save_frame.pack(fill="x", **pad)
        ttk.Button(save_frame, text="Сохранить в файл", command=self._save).pack(side="left", padx=2)
        ttk.Button(save_frame, text="Загрузить из файла", command=self._load).pack(side="left", padx=2)

        self.status = ttk.Label(self, text="Готов.", foreground="#555")
        self.status.pack(anchor="w", **pad)

    def _poll_queue(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._log(data)
                elif kind == "coords":
                    for entry in (self.x_entry, self.hold_x_entry, self.px_entry, self.py_entry):
                        entry.delete(0, tk.END)
                        entry.insert(0, str(data))
                    self.capture_active = False
                    self._log(f"Координаты заданы по клику: ({data[0]}, {data[1]})")
                elif kind == "bar_point":
                    self.capture_bar_active = False
                    x, y, color = data
                    self.bar_x_entry.delete(0, tk.END)
                    self.bar_x_entry.insert(0, str(x))
                    self.bar_y_entry.delete(0, tk.END)
                    self.bar_y_entry.insert(0, str(y))
                    self.bar_color_label.config(text=f"Цвет: {color}")
                    self._log(f"Точка шкалы: ({x}, {y}), цвет RGB{color}")
                elif kind == "key_captured":
                    self.capture_key_active = False
                    if data:
                        self.key_entry.delete(0, tk.END)
                        self.key_entry.insert(0, data)
                        self._log(f"Клавиша задана: {data}")
                    else:
                        self._log("Не задано.")
                elif kind == "stop":
                    self._stop()
                elif kind == "record_toggle":
                    self._toggle_record()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _update_cursor_label(self):
        x, y = mouse.get_position()
        self.cursor_label.config(text=f"X: {x}  Y: {y}")
        self.after(100, self._update_cursor_label)

    def _arm_click_capture(self):
        if self.capture_active:
            return
        self.capture_active = True
        mouse.hook(self._capture_click)
        self._log("Кликните в нужном месте — координаты подставятся...")

    def _capture_click(self, e):
        if not self.capture_active or e.event_type != "down":
            return
        self.capture_active = False
        try:
            mouse.unhook(self._capture_click)
        except ValueError:
            pass
        self.q.put(("coords", (e.x, e.y)))

    def _capture_key(self):
        if self.capture_key_active:
            return
        self.capture_key_active = True
        self._log("Нажмите одну клавишу — она подставится (Esc — отмена)...")

        def capture():
            key = ""
            while self.capture_key_active:
                ev = keyboard.read_event(suppress=False)
                if ev.event_type != "down":
                    continue
                if ev.name == "esc":
                    break
                if ev.name in ("f8", "f12"):
                    continue
                key = ev.name
                break
            self.q.put(("key_captured", key))

        threading.Thread(target=capture, daemon=True).start()

    def _add_click(self):
        try:
            x = int(float(self.x_entry.get()))
            y = int(float(self.y_entry.get()))
            times = max(1, int(self.click_times_entry.get()))
            interval = max(0.05, float(self.click_interval_entry.get().replace(",", ".")))
        except ValueError:
            messagebox.showwarning("Macro Bot", "Проверьте координаты, количество повторов и интервал.")
            return
        self._add_step({"type": "click", "x": x, "y": y, "button": self.button_combo.get(),
                        "times": times, "interval": interval})



    def _add_key(self):
        name = self.key_entry.get().strip().lower()
        if not name:
            messagebox.showwarning("Macro Bot", "Укажите название клавиши, например: e, space, f.")
            return
        try:
            times = max(1, int(self.key_times_entry.get()))
            hold = max(0.03, float(self.key_hold_entry.get().replace(",", ".")))
        except ValueError:
            messagebox.showwarning("Macro Bot", "Проверьте поля «Раз» и «Удерживать».")
            return
        self._add_step({"type": "key", "key": name, "times": times, "hold": hold})

    def _add_walk(self):
        raw = self.walk_keys_entry.get().strip().lower()
        keys = [k.strip() for k in raw.split("+") if k.strip()]
        if not keys:
            messagebox.showwarning("Macro Bot", "Укажите клавиши ходьбы, например: w или w+a.")
            return
        try:
            seconds = max(0.1, float(self.walk_secs_entry.get().replace(",", ".")))
        except ValueError:
            messagebox.showwarning("Macro Bot", "Введите число секунд.")
            return
        self._add_step({"type": "walk", "keys": keys, "seconds": seconds})

    def _arm_bar_capture(self):
        if self.capture_bar_active:
            return
        self.capture_bar_active = True
        mouse.hook(self._capture_bar)
        self._log("Кликните в ТОЧКУ шкалы, которая красится при заполнении...")

    def _capture_bar(self, e):
        if not self.capture_bar_active or e.event_type != "down":
            return
        self.capture_bar_active = False
        try:
            mouse.unhook(self._capture_bar)
        except ValueError:
            pass
        color = ImageGrab.grab(bbox=(e.x, e.y, e.x + 1, e.y + 1)).getpixel((0, 0))
        self.q.put(("bar_point", (e.x, e.y, tuple(color[:3]))))

    def _add_hold_button(self):
        try:
            x = int(float(self.hold_x_entry.get()))
            y = int(float(self.hold_y_entry.get()))
            seconds = max(0.1, float(self.hold_secs_entry.get().replace(",", ".")))
        except ValueError:
            messagebox.showwarning("Macro Bot", "Проверьте координаты и секунды удержания.")
            return
        self._add_step({"type": "hold_button", "x": x, "y": y,
                        "button": self.hold_button_combo.get(), "seconds": seconds})

    def _add_progress(self):
        try:
            cx = int(float(self.px_entry.get()))
            cy = int(float(self.py_entry.get()))
            bx = int(float(self.bar_x_entry.get()))
            by = int(float(self.bar_y_entry.get()))
            interval = max(0.05, float(self.p_interval_entry.get().replace(",", ".")))
            timeout = max(3, float(self.p_timeout_entry.get().replace(",", ".")))
        except ValueError:
            messagebox.showwarning("Macro Bot",
                                   "Задайте координаты нажатия и точку шкалы (по клику).")
            return
        color_text = self.bar_color_label.cget("text")
        if color_text == "Цвет: неизвестен":
            messagebox.showwarning("Macro Bot", "Сначала снимите цвет шкалы кнопкой «Снять цвет шкалы по клику».")
            return
        color = tuple(int(c) for c in color_text.replace("Цвет:", "").strip(" ()").split(","))
        mode = "hold" if self.p_mode_combo.get() == "Зажать" else "clicks"
        self._add_step({"type": "progress", "x": cx, "y": cy,
                        "button": self.p_button_combo.get(), "mode": mode,
                        "interval": interval, "timeout": timeout,
                        "bar": [bx, by], "empty_color": list(color)})

    def _is_bar_filled(self, bar_point, empty_color):
        r, g, b = ImageGrab.grab(bbox=(bar_point[0], bar_point[1],
                                       bar_point[0] + 1, bar_point[1] + 1)).getpixel((0, 0))[:3]
        return abs(r - empty_color[0]) + abs(g - empty_color[1]) + abs(b - empty_color[2]) > 60

    def _refresh_templates(self):
        names = sorted(p.name for p in TEMPLATE_DIR.glob("*.png"))
        self.tpl_combo["values"] = names
        if names and self.tpl_combo.get() not in names:
            self.tpl_combo.current(0)

    def _capture_template(self):
        if self.capturing:
            return
        self.capturing = True
        for i in range(3, 0, -1):
            self.after((3 - i) * 1000,
                       lambda n=i: self.status.config(text=f"Откройте окно с объектом... {n}"))
        self.after(3000, self._open_template_overlay)

    def _open_template_overlay(self):
        self.template_overlay = tk.Toplevel(self)
        self.template_overlay.overrideredirect(True)
        self.template_overlay.geometry(
            f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.template_overlay.attributes("-alpha", 0.3)
        self.template_overlay.attributes("-topmost", True)
        canvas = tk.Canvas(self.template_overlay, cursor="crosshair", bg="black")
        canvas.pack(fill="both", expand=True)
        canvas.bind("<ButtonPress-1>", self._tpl_start)
        canvas.bind("<B1-Motion>", self._tpl_move)
        canvas.bind("<ButtonRelease-1>", self._tpl_done)
        self.template_overlay.bind("<Escape>", lambda e: self._tpl_cancel())
        self._tpl_canvas = canvas
        self._tpl_start_pos = None
        self._log("Обведите объект рамкой (Esc — отмена)")

    def _tpl_start(self, e):
        self._tpl_start_pos = (e.x_root, e.y_root)

    def _tpl_move(self, e):
        if not self._tpl_start_pos:
            return
        ox = self.template_overlay.winfo_rootx()
        oy = self.template_overlay.winfo_rooty()
        self._tpl_canvas.delete("all")
        self._tpl_canvas.create_rectangle(
            self._tpl_start_pos[0] - ox, self._tpl_start_pos[1] - oy,
            e.x_root - ox, e.y_root - oy, outline="red", width=2)

    def _tpl_done(self, e):
        if not self._tpl_start_pos:
            return
        x1, y1 = min(self._tpl_start_pos[0], e.x_root), min(self._tpl_start_pos[1], e.y_root)
        x2, y2 = max(self._tpl_start_pos[0], e.x_root), max(self._tpl_start_pos[1], e.y_root)
        self.template_overlay.destroy()
        self.capturing = False
        if x2 - x1 < 5 or y2 - y1 < 5:
            self._log("Область слишком мала — образец не сохранён.")
            return
        n = len(list(TEMPLATE_DIR.glob("*.png"))) + 1
        path = TEMPLATE_DIR / f"template_{n}.png"
        ImageGrab.grab(bbox=(x1, y1, x2, y2)).save(path)
        self._refresh_templates()
        self.tpl_combo.set(path.name)
        self._log(f"Образец сохранён: {path.name} ({x2 - x1}x{y2 - y1})")

    def _tpl_cancel(self):
        self.template_overlay.destroy()
        self.capturing = False
        self._log("Отменено.")

    def _find_template(self, name, threshold):
        tpl = cv2.imread(str(TEMPLATE_DIR / name), cv2.IMREAD_COLOR)
        if tpl is None:
            return None
        screen = cv2.cvtColor(np.asarray(ImageGrab.grab().convert("RGB")),
                              cv2.COLOR_RGB2BGR)
        res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < threshold:
            return None
        h, w = tpl.shape[:2]
        return max_loc[0] + w // 2, max_loc[1] + h // 2

    def _add_find(self):
        name = self.tpl_combo.get()
        if not name:
            messagebox.showwarning("Macro Bot", "Сначала снимите образец рамкой.")
            return
        try:
            times = max(1, int(self.find_times_entry.get()))
            param = max(0.05, float(self.find_param_entry.get().replace(",", ".")))
        except ValueError:
            messagebox.showwarning("Macro Bot", "Проверьте поля «Раз» и «Сек».")
            return
        combo_choice = self.find_action_combo.get()
        action = "hold" if combo_choice == "Удерживать" else (
            "wait" if combo_choice == "Ждать появления" else "clicks")
        self._add_step({"type": "find", "template": name, "action": action,
                        "button": self.button_combo.get(),
                        "times": times if action == "clicks" else 1,
                        "interval": param if action == "clicks" else 0.3,
                        "hold": param if action == "hold" else 0,
                        "timeout": param if action == "wait" else 15,
                        "confidence": 0.8})

    def _add_delay(self):
        try:
            seconds = max(0.05, float(self.delay_entry.get().replace(",", ".")))
        except ValueError:
            messagebox.showwarning("Macro Bot", "Введите число секунд.")
            return
        self._add_step({"type": "delay", "seconds": seconds})

    def _add_step(self, step):
        self.steps.append(step)
        self._refresh_list()

    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for i, s in enumerate(self.steps, 1):
            if s["type"] == "click":
                times = s.get("times", 1)
                suffix = f" x{times}" if times > 1 else ""
                text = f"{i}. Клик {BUTTON_NAMES[s['button']]} в ({s['x']}, {s['y']}){suffix}"
            elif s["type"] == "key":
                times = s.get("times", 1)
                hold = s.get("hold", 0.1)
                parts = [s["key"]]
                if times > 1:
                    parts.append(f"x{times}")
                if hold > 0.15:
                    parts.append(f"удерживать {hold} сек")
                text = f"{i}. Нажать: {' '.join(parts)}"
            elif s["type"] == "walk":
                text = f"{i}. Идти: {'+'.join(s['keys'])} ({s['seconds']} сек)"
            elif s["type"] == "hold_button":
                text = (f"{i}. Удерживать {BUTTON_NAMES[s['button']]} в "
                        f"({s['x']}, {s['y']}) {s['seconds']} сек")
            elif s["type"] == "find":
                if s["action"] == "hold":
                    detail = f"удерживать {s['hold']} сек"
                elif s["action"] == "wait":
                    detail = f"ждать до {s.get('timeout', 15)} сек"
                else:
                    detail = f"{BUTTON_NAMES[s['button']]} x{s.get('times', 1)}"
                text = f"{i}. Найти «{s['template']}», {detail}"
            elif s["type"] == "progress":
                mode = "зажать" if s.get("mode", "hold") == "hold" else "кликать"
                text = (f"{i}. {mode.capitalize()} {BUTTON_NAMES[s['button']]} до заполнения "
                        f"шкалы ({tuple(s['empty_color'])}→стало иным, макс {s.get('timeout', 20)} сек)")
            else:
                text = f"{i}. Пауза: {s['seconds']} сек"
            self.listbox.insert(tk.END, text)

    def _selected_index(self):
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def _delete_step(self):
        idx = self._selected_index()
        if idx is not None:
            del self.steps[idx]
            self._refresh_list()

    def _move_step(self, direction):
        idx = self._selected_index()
        if idx is None:
            return
        new_idx = idx + direction
        if 0 <= new_idx < len(self.steps):
            self.steps[idx], self.steps[new_idx] = self.steps[new_idx], self.steps[idx]
            self._refresh_list()
            self.listbox.selection_set(new_idx)

    def _clear_all(self):
        if self.steps and not messagebox.askyesno("Macro Bot", "Удалить все шаги сценария?"):
            return
        self.steps.clear()
        self._refresh_list()

    def _toggle_record(self):
        if self.recording:
            self._stop_recording()
            self._log(f"Запись остановлена. Всего шагов: {len(self.steps)}")
            return
        mouse.hook(self._record_mouse)
        keyboard.hook(self._record_key)
        self.record_pressed = {}
        self.recording = True
        self.recording_last_t = time.perf_counter()
        self.rec_btn.config(text="■ Остановить запись")
        self._log("Идёт запись: кликайте и жмите клавиши, F8 - стоп.")

    def _stop_recording(self):
        self.recording = False
        self.rec_btn.config(text="● Запись")
        try:
            mouse.unhook(self._record_mouse)
            keyboard.unhook(self._record_key)
        except ValueError:
            pass

    def _record_mouse(self, e):
        if not self.recording:
            return
        if isinstance(e, mouse.WheelEvent):
            return
        if e.event_type == "down" and e.button in BUTTON_NAMES:
            self._record_flush({"type": "click", "x": e.x, "y": e.y, "button": e.button})

    def _record_key(self, e):
        if not self.recording or e.name in ("f8", "f12") or e.name in MODIFIER_KEYS:
            return
        if e.event_type == "down":
            if e.name in self.record_pressed:
                return
            t = time.perf_counter()
            gap = t - self.recording_last_t
            self.recording_last_t = t
            if gap > 0.25:
                self.steps.append({"type": "delay", "seconds": round(gap, 2)})
            self.record_pressed[e.name] = t
        elif e.event_type == "up":
            start = self.record_pressed.pop(e.name, None)
            if start is None:
                return
            hold = max(0.05, round(time.perf_counter() - start, 2))
            self.recording_last_t = time.perf_counter()
            self.steps.append({"type": "key", "key": e.name, "times": 1, "hold": hold})
            self.q.put(("log", f"Записано: клавиша {e.name} (удержание {hold} сек). Шагов: {len(self.steps)}"))

    def _record_flush(self, step):
        t = time.perf_counter()
        gap = t - self.recording_last_t
        self.recording_last_t = t
        if gap > 0.25:
            self.steps.append({"type": "delay", "seconds": round(gap, 2)})
        self.steps.append(step)
        self.q.put(("log", f"Записано: {step['type']}. Всего шагов: {len(self.steps)}"))

    def _start_playback(self):
        if not self.steps:
            messagebox.showwarning("Macro Bot", "Сценарий пуст: добавьте клики, клавиши или паузы.")
            return
        try:
            repeats = max(1, int(self.repeats_spin.get()))
            speed = max(0.1, float(self.speed_spin.get().replace(",", ".")))
        except ValueError:
            messagebox.showwarning("Macro Bot", "Проверьте поля «Повторов» и «Скорость».")
            return
        self.stop_event.clear()
        self.play_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        threading.Thread(target=self._play, args=(repeats, speed), daemon=True).start()

    def _play(self, repeats, speed):
        try:
            for rep in range(1, repeats + 1):
                if self.stop_event.is_set():
                    break
                self.q.put(("log", f"Прогон {rep} из {repeats}..."))
                for s in self.steps:
                    if self.stop_event.is_set():
                        return
                    self._apply_step(s, speed)
        finally:
            self.playing = False
            self.q.put(("log", "Воспроизведение остановлено."))

    def _apply_step(self, s, speed):
        if s["type"] == "click":
            for _ in range(s.get("times", 1)):
                if self.stop_event.is_set():
                    return
                mouse.move(s["x"], s["y"])
                mouse.press(s["button"])
                time.sleep(KEY_PRESS_PAUSE)
                mouse.release(s["button"])
                self.stop_event.wait(s.get("interval", 0.3) / speed)
        elif s["type"] == "key":
            for _ in range(s.get("times", 1)):
                if self.stop_event.is_set():
                    return
                keyboard.press(s["key"])
                time.sleep(s.get("hold", 0.1) / speed)
                keyboard.release(s["key"])
                time.sleep(KEY_PRESS_PAUSE)
        elif s["type"] == "hold_button":
            mouse.move(s["x"], s["y"])
            mouse.press(s["button"])
            self.stop_event.wait(s["seconds"] / speed)
            mouse.release(s["button"])
        elif s["type"] == "find":
            started = time.perf_counter()
            pos = None
            while not self.stop_event.is_set():
                if (time.perf_counter() - started) > s.get("timeout", 15):
                    self.q.put(("log", f"Образец {s['template']} не найден — шаг пропущен."))
                    break
                pos = self._find_template(s["template"], s.get("confidence", 0.8))
                if pos:
                    self.q.put(("log", f"Найдено: {s['template']} в {pos}"))
                    break
                self.stop_event.wait(0.3)
            if pos is None:
                return
            if s["action"] == "wait":
                return
            x, y = pos
            if s["action"] == "hold":
                mouse.move(x, y)
                mouse.press(s["button"])
                self.stop_event.wait(s["hold"] / speed)
                mouse.release(s["button"])
            else:
                for _ in range(s.get("times", 1)):
                    if self.stop_event.is_set():
                        return
                    mouse.move(x, y)
                    mouse.press(s["button"])
                    time.sleep(KEY_PRESS_PAUSE)
                    mouse.release(s["button"])
                    self.stop_event.wait(s.get("interval", 0.3) / speed)
        elif s["type"] == "progress":
            started = time.perf_counter()
            mode_hold = s.get("mode", "hold") == "hold"
            if mode_hold:
                mouse.move(s["x"], s["y"])
                mouse.press(s["button"])
            while not self.stop_event.is_set():
                if (time.perf_counter() - started) > s.get("timeout", 20):
                    self.q.put(("log", "Шкала не заполнилась — сработал таймер страховки."))
                    break
                if self._is_bar_filled(s["bar"], s["empty_color"]):
                    self.q.put(("log", "Шкала заполнена."))
                    break
                if not mode_hold:
                    mouse.move(s["x"], s["y"])
                    mouse.press(s["button"])
                    time.sleep(KEY_PRESS_PAUSE)
                    mouse.release(s["button"])
                self.stop_event.wait(s.get("interval", 0.25) / speed)
            if mode_hold:
                mouse.release(s["button"])
        elif s["type"] == "walk":
            for k in s["keys"]:
                keyboard.press(k)
            if self.stop_event.wait(s["seconds"] / speed):
                pass
            for k in reversed(s["keys"]):
                keyboard.release(k)
        else:
            self.stop_event.wait(s["seconds"] / speed)

    def _stop(self):
        self.stop_event.set()
        if self.recording:
            self._stop_recording()
        self.play_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self._log("Остановлено.")

    def _save(self):
        MACRO_FILE.write_text(json.dumps(self.steps, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        self._log(f"Сохранено: {MACRO_FILE.name} ({len(self.steps)} шагов)")

    def _load(self):
        if not MACRO_FILE.exists():
            messagebox.showinfo("Macro Bot", f"Файл {MACRO_FILE.name} не найден.")
            return
        try:
            self.steps = json.loads(MACRO_FILE.read_text(encoding="utf-8"))
            self._refresh_list()
            self._log(f"Загружено шагов: {len(self.steps)}")
        except (json.JSONDecodeError, KeyError):
            messagebox.showerror("Macro Bot", "Файл сценария повреждён.")

    def _load_silent(self):
        if MACRO_FILE.exists():
            try:
                self.steps = json.loads(MACRO_FILE.read_text(encoding="utf-8"))
                self._refresh_list()
            except (json.JSONDecodeError, KeyError):
                pass

    def _log(self, text):
        self.status.config(text=text)

    def _on_close(self):
        self.stop_event.set()
        try:
            mouse.unhook_all()
            keyboard.unhook_all()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    MacroApp().mainloop()
