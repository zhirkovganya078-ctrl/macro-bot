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

import keyboard
import mouse

MACRO_FILE = Path(__file__).with_name("macro.json")
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
        ttk.Button(click_frame, text="Добавить клик", command=self._add_click).grid(row=0, column=6, padx=8)

        key_frame = ttk.LabelFrame(self, text="Нажатие клавиши")
        key_frame.pack(fill="x", **pad)
        ttk.Label(key_frame, text="Клавиша:").grid(row=0, column=0, padx=6, pady=6)
        self.key_entry = ttk.Entry(key_frame, width=12)
        self.key_entry.grid(row=0, column=1, padx=2)
        ttk.Label(key_frame, text="например: e, space, f").grid(row=0, column=2, padx=6)
        ttk.Button(key_frame, text="Добавить клавишу", command=self._add_key).grid(row=0, column=3, padx=8)

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
                    self.x_entry.delete(0, tk.END)
                    self.x_entry.insert(0, str(data[0]))
                    self.y_entry.delete(0, tk.END)
                    self.y_entry.insert(0, str(data[1]))
                    self.capture_active = False
                    self._log(f"Координаты заданы по клику: ({data[0]}, {data[1]})")
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

    def _add_click(self):
        try:
            x = int(float(self.x_entry.get()))
            y = int(float(self.y_entry.get()))
        except ValueError:
            messagebox.showwarning("Macro Bot", "Введите числовые координаты X и Y.")
            return
        self._add_step({"type": "click", "x": x, "y": y, "button": self.button_combo.get()})



    def _add_key(self):
        name = self.key_entry.get().strip().lower()
        if not name:
            messagebox.showwarning("Macro Bot", "Укажите название клавиши, например: e, space, f.")
            return
        self._add_step({"type": "key", "key": name})

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
                text = f"{i}. Клик {BUTTON_NAMES[s['button']]} в ({s['x']}, {s['y']})"
            elif s["type"] == "key":
                text = f"{i}. Нажать: {s['key']}"
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
        if not self.recording or e.event_type != "down":
            return
        if e.name in ("f8", "f12") or e.name in MODIFIER_KEYS:
            return
        self._record_flush({"type": "key", "key": e.name})

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
            mouse.move(s["x"], s["y"])
            mouse.press(s["button"])
            time.sleep(KEY_PRESS_PAUSE)
            mouse.release(s["button"])
        elif s["type"] == "key":
            keyboard.press(s["key"])
            time.sleep(KEY_PRESS_PAUSE)
            keyboard.release(s["key"])
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
