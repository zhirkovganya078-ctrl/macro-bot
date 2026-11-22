"""AHK Bot: triggers on mouse wheel / middle button, like an AutoHotkey script.

WheelDown -> press Alt.
WheelUp -> click at (X, Y), walk S 1s, walk W 1s, click again.
MButton -> LMB click, 4x RMB hold 2s, 36 fast LMB clicks, 4x RMB hold 1.7s.

F12 stops a running sequence, Ins hides/shows the window, window is topmost.
Run as Administrator if the game ignores synthetic input.
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import keyboard
import mouse

CLICK_X_DEFAULT = 1150
CLICK_Y_DEFAULT = 380
KEY_PAUSE = 0.05


class AhkBot(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AHK Bot")
        self.geometry("340x360")
        self.minsize(300, 280)
        self.attributes("-topmost", True)

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.hidden = False
        self.running_wheeldown = False
        self.running_wheelup = False
        self.running_mbutton = False
        self.active_cancels = []
        self.enabled = {"wheeldown": True, "wheelup": True, "mbutton": True}
        self.click_xy = (CLICK_X_DEFAULT, CLICK_Y_DEFAULT)
        self.hook_alive_logged = False

        self._build_ui()

        try:
            keyboard.add_hotkey("f12", self._on_f12)
            keyboard.add_hotkey("insert", lambda: self.q.put(("hide_toggle", None)))
        except Exception:
            self.after(200, lambda: self._log("Клавиатурные хуки не заработали — запустите от имени администратора"))
        try:
            mouse.hook(self._on_mouse)
        except Exception:
            self.after(200, lambda: self._log("Хуки мыши не заработали — запустите от имени администратора"))
        self.after(100, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._sync_state)

    def _sync_state(self):
        self.enabled["wheeldown"] = bool(self.wheeldown_var.get())
        self.enabled["wheelup"] = bool(self.wheelup_var.get())
        self.enabled["mbutton"] = bool(self.mbutton_var.get())
        try:
            self.click_xy = (int(float(self.x_entry.get())),
                             int(float(self.y_entry.get())))
        except ValueError:
            pass
        self.after(100, self._sync_state)

    def _build_ui(self):
        frame = ttk.LabelFrame(self, text="Триггеры (включены галочкой)")
        frame.pack(fill="x", padx=8, pady=8)
        self.wheeldown_var = tk.BooleanVar(value=True)
        self.wheelup_var = tk.BooleanVar(value=True)
        self.mbutton_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Колёсико ВНИЗ → нажать Alt",
                        variable=self.wheeldown_var).pack(anchor="w", padx=8, pady=3)
        ttk.Checkbutton(frame, text="Колёсико ВВЕРХ → клик, ходьба S/W, клик",
                        variable=self.wheelup_var).pack(anchor="w", padx=8, pady=3)
        ttk.Checkbutton(frame, text="Средняя кнопка → болты ( ПКМ зажимы + клики )",
                        variable=self.mbutton_var).pack(anchor="w", padx=8, pady=3)

        coord = ttk.LabelFrame(self, text="Точка клика (для колёсика вверх)")
        coord.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(coord, text="X:").grid(row=0, column=0, padx=6, pady=6)
        self.x_entry = ttk.Entry(coord, width=7)
        self.x_entry.insert(0, str(CLICK_X_DEFAULT))
        self.x_entry.grid(row=0, column=1)
        ttk.Label(coord, text="Y:").grid(row=0, column=2, padx=6)
        self.y_entry = ttk.Entry(coord, width=7)
        self.y_entry.insert(0, str(CLICK_Y_DEFAULT))
        self.y_entry.grid(row=0, column=3)

        log_frame = ttk.LabelFrame(self, text="Журнал")
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=6, state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self.topmost_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom, text="Поверх всех окон", variable=self.topmost_var,
                        command=lambda: self.attributes("-topmost",
                                                        bool(self.topmost_var.get()))
                        ).pack(side="left")
        ttk.Button(bottom, text="Тест всех команд", command=self._test_all).pack(side="left", padx=12)
        ttk.Label(bottom, text="Ins — скрыть окно, F12 — стоп",
                  foreground="#777").pack(side="right")

    def _log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert("end", time.strftime("%H:%M:%S ") + text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_queue(self):
        while True:
            try:
                kind, data = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "hide_toggle":
                self.hidden = not self.hidden
                if self.hidden:
                    self.withdraw()
                else:
                    self.deiconify()
            elif kind == "log":
                self._log(data)
        self.after(100, self._poll_queue)

    def _on_f12(self):
        self.stop_event.set()
        for c in list(self.active_cancels):
            c.set()
        self.q.put(("log", "F12: остановлено."))

    def _on_mouse(self, e):
        try:
            if not self.hook_alive_logged:
                self.hook_alive_logged = True
                self._log_safe("Хук мыши работает: события приходят")
            if self.running_mbutton and self._is_our_click(e):
                return
            button = getattr(e, "button", None)
            if button == "middle" and getattr(e, "event_type", None) == "down":
                if self.enabled["mbutton"] and not self.running_mbutton:
                    self._log_safe("Триггер: средняя кнопка")
                    threading.Thread(target=self.seq_mbutton, daemon=True).start()
                return
            delta = getattr(e, "delta", None)
            if not delta:
                return
            if delta > 0:
                if self.enabled["wheelup"] and not self.running_wheelup:
                    self._log_safe("Триггер: колесико вверх")
                    threading.Thread(target=self.seq_wheelup, daemon=True).start()
            else:
                if self.enabled["wheeldown"] and not self.running_wheeldown:
                    self._log_safe("Триггер: колесико вниз")
                    threading.Thread(target=self.seq_wheeldown, daemon=True).start()
        except Exception as err:
            self._log_safe(f"Ошибка хука мыши: {err}")

    def _is_our_click(self, e):
        return getattr(e, "event_type", None) in ("down", "up")

    def _wait(self, seconds, kind):
        return self.stop_event.wait(seconds)

    def _press(self, key):
        keyboard.press(key)
        self.stop_event.wait(KEY_PAUSE)
        keyboard.release(key)

    def _click(self, button):
        mouse.press(button)
        self.stop_event.wait(0.1)
        mouse.release(button)

    def _point(self):
        return self.click_xy

    def _finish(self, flag_name):
        setattr(self, flag_name, False)

    def _press_left_alt(self):
        for name in ("left alt", "alt"):
            try:
                keyboard.key_to_scan_codes(name)
                keyboard.press(name)
                self.stop_event.wait(KEY_PAUSE)
                keyboard.release(name)
                return name
            except (ValueError, KeyError):
                continue
        return None

    def seq_wheeldown(self):
        self.running_wheeldown = True
        try:
            if self.stop_event.is_set():
                self.stop_event.clear()
            used = self._press_left_alt()
            if used:
                self._log_safe("Колесико вниз: нажат ЛЕВЫЙ Alt")
            else:
                self._log_safe("Колесико вниз: не смог послать Alt")
            self._log_safe("Колёсико вниз: Alt")
        finally:
            self._finish("running_wheeldown")

    def seq_wheelup(self):
        self.running_wheelup = True
        cancel = threading.Event()
        self.active_cancels.append(cancel)
        try:
            if self.stop_event.is_set():
                self.stop_event.clear()
            x, y = self._point()
            self.stop_event.wait(0.05)
            mouse.move(x, y)
            self.stop_event.wait(0.1)
            self._click("left")
            keyboard.press("s")
            self.stop_event.wait(1.0)
            keyboard.release("s")
            self.stop_event.wait(0.1)
            keyboard.press("w")
            self.stop_event.wait(1.0)
            keyboard.release("w")
            self.stop_event.wait(0.05)
            mouse.move(x, y)
            self.stop_event.wait(0.1)
            self._click("left")
            self.stop_event.wait(0.1)
            self._log_safe(f"Колёсико вверх: выполнено (клик {x},{y}, ходьба S/W)")
        finally:
            self.active_cancels.remove(cancel)
            self._finish("running_wheelup")

    def seq_mbutton(self):
        self.running_mbutton = True
        cancel = threading.Event()
        self.active_cancels.append(cancel)
        try:
            if self.stop_event.is_set():
                self.stop_event.clear()
            self._click("left")
            for _ in range(4):
                mouse.press("right")
                self.stop_event.wait(2.0)
                mouse.release("right")
                self.stop_event.wait(0.1)
                if self.stop_event.is_set():
                    return
            for _ in range(36):
                mouse.press("left")
                self.stop_event.wait(0.01)
                mouse.release("left")
                self.stop_event.wait(0.01)
                if self.stop_event.is_set():
                    return
            for _ in range(4):
                mouse.press("right")
                self.stop_event.wait(1.7)
                mouse.release("right")
                self.stop_event.wait(0.1)
                if self.stop_event.is_set():
                    return
            self._log_safe("Средняя кнопка: болты выполнены")
        finally:
            self.active_cancels.remove(cancel)
            self._finish("running_mbutton")

    def _on_close(self):
        self.stop_event.set()
        for c in list(self.active_cancels):
            c.set()
        try:
            mouse.unhook_all()
            keyboard.unhook_all()
        except Exception:
            pass
        self.destroy()

    def _test_all(self):
        threading.Thread(target=self._test_worker, daemon=True).start()

    def _test_worker(self):
        self._log_safe("ТЕСТ 1: левый Alt (колесико вниз)...")
        self.seq_wheeldown()
        self.stop_event.wait(1.0)
        self._log_safe("ТЕСТ 2: колесико вверх (клик + ходьба S/W)...")
        self.seq_wheelup()
        self.stop_event.wait(1.0)
        self._log_safe("ТЕСТ 3: средняя кнопка (болты)...")
        self.seq_mbutton()
        self._log_safe("ТЕСТ завершён — проверьте игру")

    def _log_safe(self, msg):
        self.q.put(("log", msg))


if __name__ == "__main__":
    try:
        AhkBot().mainloop()
    except Exception:
        import traceback
        traceback.print_exc()
        input("Произошла ошибка — сделайте скриншот этого окна и отправьте мне. "
              "Enter — закрыть...")
