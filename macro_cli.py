"""Macro Bot: record mouse clicks and keyboard input on Windows, then replay them.

Hotkeys:
    F8  - start / stop recording
    F9  - play recorded macro (repeats REPEATS times, loop if set)
    F10 - save recording to file
    F12 - emergency stop (aborts playback/recording)

Run as Administrator if the game ignores synthetic input.
"""

import json
import sys
import threading
import time
from pathlib import Path

import keyboard
import mouse

MACRO_FILE = Path(__file__).with_name("macro.json")

REPEATS = 1
SPEED = 1.0
MIN_DELAY = 0.01

stop_event = threading.Event()
recording = False
playing = False
events = []


def now():
    return time.perf_counter()


def record_mouse(e):
    if not recording:
        return
    if isinstance(e, mouse.WheelEvent):
        events.append({"t": now() - record_start, "type": "wheel", "delta": -e.delta})
    elif e.event_type == "down" and e.button in ("left", "right", "middle"):
        events.append({"t": now() - record_start, "type": "click", "button": e.button, "x": e.x, "y": e.y})


def record_key(e):
    if not recording or e.event_type != "down":
        return
    name = e.name
    if name in ("f8", "f9", "f10", "f12"):
        return
    t = now() - record_start
    events.append({"t": t, "type": "key", "key": name})


def start_recording():
    global recording, record_start
    events.clear()
    record_start = now()
    recording = True
    print("[*] Запись начата. F8 - остановить.")


def stop_recording():
    global recording
    recording = False
    print(f"[*] Запись остановлена. Событий: {len(events)}")


def toggle_record():
    if recording:
        stop_recording()
    else:
        start_recording()


def play_macro():
    global playing
    if not events:
        print("[!] Макрос пуст. Сначала запишите действия (F8).")
        return
    playing = True
    print(f"[*] Воспроизведение: {len(events)} событий, повторов: {REPEATS}, скорость: {SPEED}x")
    try:
        for rep in range(REPEATS):
            if stop_event.is_set():
                break
            t0 = now()
            for ev in events:
                if stop_event.is_set():
                    break
                wait = ev["t"] / SPEED - (now() - t0)
                if wait > MIN_DELAY:
                    stop_event.wait(wait)
                apply_event(ev)
            if REPEATS > 1:
                print(f"    повтор {rep + 1}/{REPEATS}")
    finally:
        playing = False
        print("[*] Воспроизведение завершено.")


def apply_event(ev):
    if ev["type"] == "click":
        mouse.move(ev["x"], ev["y"])
        mouse.press(ev["button"])
        time.sleep(0.02)
        mouse.release(ev["button"])
    elif ev["type"] == "wheel":
        mouse.wheel(0, ev["delta"])
    elif ev["type"] == "key":
        keyboard.press(ev["key"])
        time.sleep(0.02)
        keyboard.release(ev["key"])


def save_macro():
    if not events:
        print("[!] Нечего сохранять.")
        return
    MACRO_FILE.write_text(json.dumps(events, indent=2), encoding="utf-8")
    print(f"[*] Сохранено: {MACRO_FILE} ({len(events)} событий)")


def load_macro():
    if MACRO_FILE.exists():
        global events
        events = json.loads(MACRO_FILE.read_text(encoding="utf-8"))
        print(f"[*] Загружен макрос из {MACRO_FILE} ({len(events)} событий)")


def emergency_stop():
    stop_event.set()
    if recording:
        stop_recording()
    print("[!] Аварийная остановка. F9 снова - новое воспроизведение.")


def on_f9():
    stop_event.clear()
    if not playing:
        threading.Thread(target=play_macro, daemon=True).start()


def main():
    print("=== Macro Bot ===")
    print("F8 - запись/стоп | F9 - воспроизвести | F10 - сохранить | F12 - аварийный стоп")
    load_macro()

    mouse.on_button(record_mouse, args=())
    keyboard.on_press(record_key)

    keyboard.add_hotkey("f8", toggle_record)
    keyboard.add_hotkey("f9", on_f9)
    keyboard.add_hotkey("f10", save_macro)
    keyboard.add_hotkey("f12", emergency_stop)

    try:
        keyboard.wait("ctrl+esc")
    except KeyboardInterrupt:
        pass
    print("[*] Выход.")


if __name__ == "__main__":
    main()
