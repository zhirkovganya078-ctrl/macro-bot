"""Vision Bot: AI-vision watcher for Windows.

Periodically screenshots the screen, asks a vision model (Mistral API)
whether the target is visible, and reacts: clicks the found point
and/or presses configured keys.

Requirements: a Mistral API key from console.mistral.ai (free tier works).
Run as Administrator if the game ignores synthetic input. F12 stops it.
"""

import base64
import io
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

import keyboard
import mouse
import requests
from PIL import Image, ImageGrab

CONFIG_FILE = Path(__file__).with_name("vision_config.json")
REFERENCE_FILE = Path(__file__).with_name("reference.png")
API_URL = "https://api.mistral.ai/v1/chat/completions"
KEY_PRESS_PAUSE = 0.03
MAX_SCREEN_WIDTH = 1280
JPEG_QUALITY = 70
DEFAULT_MODEL = "pixtral-large-latest"

def build_prompt(target: str, has_reference: bool) -> str:
    images = "текущий скриншот экрана"
    if has_reference:
        images += " и эталонный образец цели (второе изображение — эталон)"
    return (
        "Ты — модуль компьютерного зрения для игрового бота. "
        f"На изображениях ниже: {images}. "
        f"Задача: определить, есть ли на скриншоте экрана цель: {target}. "
        "Если цель есть, найди центр объекта. "
        "Ответь ТОЛЬКО строго одним JSON-объектом без пояснений:\n"
        '{"found": true, "x": <0..1>, "y": <0..1>, "comment": "<кратко, что нашёл>"}\n'
        "где x и y — координаты центра цели в долях размера скриншота "
        "(0.0 — левый/верхний край, 1.0 — правый/нижний). "
        'Если цели нет или ты не уверен, верни '
        '{"found": false, "x": 0, "y": 0, "comment": "<почему>"}.'
    )


class VisionApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Vision Bot — ИИ-зрение")
        self.geometry("560x620")
        self.minsize(500, 540)

        self.stop_event = threading.Event()
        self.watching = False
        self.capturing = False

        self.q = queue.Queue()
        self._build_ui()
        self._load_config()

        keyboard.add_hotkey("f12", lambda: self.q.put(("stop", None)))
        self.after(100, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        api_frame = ttk.LabelFrame(self, text="Mistral API (vision-модель)")
        api_frame.pack(fill="x", **pad)
        ttk.Label(api_frame, text="API-ключ:").grid(row=0, column=0, padx=6, pady=6)
        self.key_entry = ttk.Entry(api_frame, width=42, show="*")
        self.key_entry.grid(row=0, column=1, padx=2)
        ttk.Label(api_frame, text="Модель:").grid(row=1, column=0, padx=6)
        self.model_entry = ttk.Entry(api_frame, width=24)
        self.model_entry.insert(0, DEFAULT_MODEL)
        self.model_entry.grid(row=1, column=1, padx=2, sticky="w")

        ref_frame = ttk.LabelFrame(self, text="Эталонный образец цели (необязательно, но повышает точность)")
        ref_frame.pack(fill="x", **pad)
        self.ref_status = ttk.Label(
            ref_frame,
            text="Образец не задан" if not REFERENCE_FILE.exists() else f"Образец: {REFERENCE_FILE.name}",
        )
        self.ref_status.pack(anchor="w", padx=8, pady=(4, 0))
        self.ref_btn = ttk.Button(ref_frame, text="Захватить образец (3 сек на переключение в игру)",
                                  command=self._capture_reference)
        self.ref_btn.pack(anchor="w", padx=8, pady=(2, 6))

        target_frame = ttk.LabelFrame(self, text="Что искать на экране")
        target_frame.pack(fill="x", **pad)
        self.target_text = tk.Text(target_frame, height=2, wrap="word")
        self.target_text.pack(fill="x", padx=8, pady=6)
        self.target_text.insert("1.0", "красная кнопка «Продать» в правом нижнем углу")

        react_frame = ttk.LabelFrame(self, text="Реакция на появление цели")
        react_frame.pack(fill="x", **pad)
        self.click_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(react_frame, text="Кликнуть по центру цели", variable=self.click_var).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=6, pady=4)
        ttk.Label(react_frame, text="Кнопка мыши:").grid(row=1, column=0, padx=6)
        self.button_combo = ttk.Combobox(react_frame, width=8, state="readonly",
                                         values=["left", "right", "middle"])
        self.button_combo.current(0)
        self.button_combo.grid(row=1, column=1, padx=2)
        ttk.Label(react_frame, text="Нажать клавиши (через запятую):").grid(row=2, column=0, padx=6, pady=4)
        self.keys_entry = ttk.Entry(react_frame, width=24)
        self.keys_entry.grid(row=2, column=1, padx=2, sticky="w")

        watch_frame = ttk.LabelFrame(self, text="Проверка экрана")
        watch_frame.pack(fill="x", **pad)
        ttk.Label(watch_frame, text="Интервал, сек:").grid(row=0, column=0, padx=6, pady=6)
        self.interval_spin = ttk.Spinbox(watch_frame, from_=1, to=60, width=6)
        self.interval_spin.set(3)
        self.interval_spin.grid(row=0, column=1, padx=2)
        self.watch_btn = ttk.Button(watch_frame, text="▶ Запустить слежение", command=self._toggle_watch)
        self.watch_btn.grid(row=0, column=2, padx=8)
        ttk.Label(watch_frame, text="F12 — аварийный стоп").grid(row=0, column=3, padx=8)

        log_frame = ttk.LabelFrame(self, text="Журнал")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=10, wrap="word", state="disabled",
                                font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=8, pady=6)

        self.status = ttk.Label(self, text="Готов.", foreground="#555")
        self.status.pack(anchor="w", **pad)

    def _poll_queue(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._log(data)
                elif kind == "status":
                    self.status.config(text=data)
                elif kind == "watch_state":
                    self.watching = data
                    self.watch_btn.config(text="■ Остановить слежение" if data else "▶ Запустить слежение")
                elif kind == "stop":
                    self._stop_watch()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def _save_config(self):
        CONFIG_FILE.write_text(json.dumps({
            "api_key": self.key_entry.get().strip(),
            "model": self.model_entry.get().strip(),
        }), encoding="utf-8")
        self.q.put(("log", f"Ключ сохранён локально: {CONFIG_FILE.name} (не публикуйте его)"))

    def _load_config(self):
        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                self.key_entry.insert(0, cfg.get("api_key", ""))
                self.model_entry.delete(0, tk.END)
                self.model_entry.insert(0, cfg.get("model") or DEFAULT_MODEL)
            except json.JSONDecodeError:
                pass

    def _capture_reference(self):
        if self.capturing:
            return
        self.capturing = True
        for i in range(3, 0, -1):
            self.after((3 - i) * 1000, lambda n=i: self.status.config(
                text=f"Откройте окно с целью... {n}"))
        self.after(3000, self._open_overlay)

    def _open_overlay(self):
        self.overlay = tk.Toplevel(self)
        self.overlay.overrideredirect(True)
        self.overlay.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.overlay.attributes("-alpha", 0.3)
        self.overlay.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.overlay, cursor="crosshair", bg="black")
        self.canvas.pack(fill="both", expand=True)
        self.rect = None
        self.start_x = self.start_y = 0
        self.canvas.bind("<ButtonPress-1>", self._sel_start)
        self.canvas.bind("<B1-Motion>", self._sel_move)
        self.canvas.bind("<ButtonRelease-1>", self._sel_done)
        self.overlay.bind("<Escape>", lambda e: self._sel_cancel())
        self.q.put(("status", "Обведите цель мышью (Esc — отмена)"))

    def _sel_start(self, e):
        self.start_x, self.start_y = e.x_root, e.y_root
        self.canvas.delete("all")

    def _sel_move(self, e):
        self.canvas.delete("all")
        self.canvas.create_rectangle(
            self.start_x - self.overlay.winfo_rootx(),
            self.start_y - self.overlay.winfo_rooty(),
            e.x_root - self.overlay.winfo_rootx(),
            e.y_root - self.overlay.winfo_rooty(),
            outline="red", width=2)

    def _sel_done(self, e):
        x1, y1 = min(self.start_x, e.x_root), min(self.start_y, e.y_root)
        x2, y2 = max(self.start_x, e.x_root), max(self.start_y, e.y_root)
        self.overlay.destroy()
        self.capturing = False
        if x2 - x1 < 5 or y2 - y1 < 5:
            self.q.put(("status", "Область слишком мала — образец не сохранён."))
            return
        shot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
        shot.save(REFERENCE_FILE)
        self.ref_status.config(text=f"Образец: {REFERENCE_FILE.name} ({x2 - x1}x{y2 - y1})")
        self.q.put(("status", "Образец сохранён."))

    def _sel_cancel(self):
        self.overlay.destroy()
        self.capturing = False
        self.q.put(("status", "Отменено."))

    def _toggle_watch(self):
        if self.watching:
            self._stop_watch()
            return
        if not self.key_entry.get().strip():
            messagebox.showwarning("Vision Bot", "Введите Mistral API-ключ (console.mistral.ai).")
            return
        target = self.target_text.get("1.0", tk.END).strip()
        if not target:
            messagebox.showwarning("Vision Bot", "Опишите, что искать на экране.")
            return
        self._save_config()
        self.stop_event.clear()
        self.q.put(("watch_state", True))
        threading.Thread(target=self._watch, args=(target,), daemon=True).start()

    def _stop_watch(self):
        self.stop_event.set()
        self.q.put(("watch_state", False))
        self.q.put(("log", "Слежение остановлено."))

    def _watch(self, target):
        try:
            interval = max(1, float(self.interval_spin.get().replace(",", ".")))
        except ValueError:
            interval = 3.0
        keys = [k.strip().lower() for k in self.keys_entry.get().split(",") if k.strip()]
        while not self.stop_event.is_set():
            started = time.perf_counter()
            found = self._check_once(target)
            if found and keys:
                for k in keys:
                    if self.stop_event.is_set():
                        break
                    keyboard.press(k)
                    time.sleep(KEY_PRESS_PAUSE)
                    keyboard.release(k)
                    time.sleep(0.2)
            elapsed = time.perf_counter() - started
            self.stop_event.wait(max(0.2, interval - elapsed))

    def _grab_screen_b64(self):
        shot = ImageGrab.grab()
        screen_w, screen_h = shot.size
        if shot.width > MAX_SCREEN_WIDTH:
            ratio = MAX_SCREEN_WIDTH / shot.width
            shot = shot.resize((MAX_SCREEN_WIDTH, int(shot.height * ratio)))
        buf = io.BytesIO()
        shot.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode(), screen_w, screen_h

    def _check_once(self, target):
        try:
            screen_b64, w, h = self._grab_screen_b64()
            content = [{"type": "text",
                        "text": build_prompt(target, REFERENCE_FILE.exists())}]
            content.append({"type": "image_url",
                            "image_url": f"data:image/jpeg;base64,{screen_b64}"})
            if REFERENCE_FILE.exists():
                ref_b64 = base64.b64encode(REFERENCE_FILE.read_bytes()).decode()
                content.append({"type": "image_url",
                                "image_url": f"data:image/png;base64,{ref_b64}"})
            model = self.model_entry.get().strip() or DEFAULT_MODEL
            resp = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {self.key_entry.get().strip()}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0,
                    "max_tokens": 300,
                },
                timeout=30,
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip()
            return self._handle_answer(answer, w, h)
        except requests.RequestException as e:
            self.q.put(("log", f"Ошибка сети/API: {e}"))
        except (KeyError, IndexError, ValueError) as e:
            self.q.put(("log", f"Неожиданный формат ответа: {e}"))
        return False

    def _handle_answer(self, answer, w, h):
        try:
            start = answer.index("{")
            end = answer.rindex("}") + 1
            data = json.loads(answer[start:end])
        except (ValueError, json.JSONDecodeError):
            self.q.put(("log", f"Модель ответила без JSON: {answer[:120]}"))
            return False
        comment = data.get("comment", "")
        if not data.get("found"):
            self.q.put(("log", f"Цель не найдена. {comment}"))
            return False
        x = float(data.get("x", 0)) * w
        y = float(data.get("y", 0)) * h
        self.q.put(("log", f"Цель найдена в ({int(x)}, {int(y)}). {comment}"))
        if self.click_var.get():
            button = self.button_combo.get()
            mouse.move(int(x), int(y))
            mouse.press(button)
            time.sleep(KEY_PRESS_PAUSE)
            mouse.release(button)
        return True

    def _on_close(self):
        self.stop_event.set()
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    VisionApp().mainloop()
