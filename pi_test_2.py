"""
Speed Snooker Timer UI (single-file baseline)

Update in this version:
- Bluetooth fob input no longer relies on /dev/input/eventX (unstable across reboots).
- Uses evdev auto-discovery:
    - Finds an input device that supports KEY_VOLUMEUP (code 115)
    - Optionally prefers devices whose name contains one of FOB_NAME_HINTS
    - Reconnects automatically if the device disappears/reconnects
- Injects a pygame custom event (FOB_TRIGGER_EVENT) into the main loop.
"""

import sys
import time
import math
import threading
import os
from array import array
from dataclasses import dataclass
from typing import Optional

import pygame


# ============================================================
# CONFIG SECTION
# ============================================================

RESOLUTION = (1920, 1080)
FPS = 60

GAME_OPTIONS = [
    ("30 MINUTES", 30 * 60),
    ("20 MINUTES", 20 * 60),
    ("15 MINUTES", 15 * 60),
    ("5:30", 5 * 60 + 30),
]

FINAL_PHASE_SECONDS = 5 * 60
SHOT_CLOCK_NORMAL_SECONDS = 15
SHOT_CLOCK_FINAL_SECONDS = 10

AUDIO_ENABLED = True
SAMPLE_RATE = 44100
BEEP_FREQ_HZ = 880
BEEP_SHORT_MS = 120
BEEP_LONG_MS = 3000
BEEP_VOLUME = 0.6

# --- Fob input (Raspberry Pi OS) ---
# If you have a stable by-id path, put it here (recommended). Example:
# FOB_DEVICE_PATH = "/dev/input/by-id/usb-...-event-kbd"
# Leave as None to use auto-discovery.
FOB_DEVICE_PATH: Optional[str] = None

# If auto-discovery is used, prefer devices whose name contains one of these substrings (case-insensitive).
# Add your fob name fragments here after checking `sudo evtest` device list.
FOB_NAME_HINTS = [
    "bluetooth",
    "camera",
    "shutter",
    "remote",
]

# The fob emits KEY_VOLUMEUP (code 115). Trigger only on key-down (value 1).
FOB_KEY_CODE = 115

# Custom pygame event used to inject fob presses into the main loop.
FOB_TRIGGER_EVENT = pygame.USEREVENT + 1

COLORS = {
    "bg": (40, 120, 40),
    "fg": (240, 240, 240),
    "accent": (80, 160, 255),
    "dim": (120, 120, 120),
    "panel": (16, 16, 16),
    "shot": (255, 255, 255),
    "shot_critical": (220, 40, 40),
}


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def fmt_time(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    m = total_seconds // 60
    s = total_seconds % 60
    return f"{m:02d}:{s:02d}"


def draw_centered_text(surface, font, text, center, color):
    img = font.render(text, True, color)
    rect = img.get_rect(center=center)
    surface.blit(img, rect)


def make_tone_sound(freq_hz: int, duration_ms: int, volume: float) -> pygame.mixer.Sound:
    n_samples = int(SAMPLE_RATE * (duration_ms / 1000.0))
    amp = int(32767 * max(0.0, min(1.0, volume)))
    buf = array("h")
    step = (2.0 * math.pi * freq_hz) / SAMPLE_RATE

    fade = min(200, n_samples // 10)
    for i in range(n_samples):
        v = math.sin(step * i)
        sample = int(amp * v)

        if fade > 0:
            if i < fade:
                sample = int(sample * (i / fade))
            elif i > n_samples - fade:
                sample = int(sample * ((n_samples - i) / fade))

        buf.append(sample)

    return pygame.mixer.Sound(buffer=buf.tobytes())


# ============================================================
# DATA MODEL FOR MENU BUTTONS
# ============================================================

@dataclass
class Button:
    label: str
    rect: pygame.Rect
    seconds: int


# ============================================================
# TIMER / STATE LOGIC
# ============================================================

class FrameController:
    def __init__(self, frame_total_seconds: int):
        self.frame_remaining = int(frame_total_seconds)
        self.shot_remaining = 0
        self.running = False

        self._last_tick = time.monotonic()
        self._accum = 0.0
        self._prev_shot_remaining = 0

    def _current_shot_length(self) -> int:
        if self.frame_remaining <= FINAL_PHASE_SECONDS:
            return SHOT_CLOCK_FINAL_SECONDS
        return SHOT_CLOCK_NORMAL_SECONDS

    def toggle_run(self) -> None:
        if self.frame_remaining <= 0:
            self.running = False
            self.shot_remaining = 0
            self._accum = 0.0
            self._prev_shot_remaining = 0
            return

        if self.running:
            self.running = False
            self.shot_remaining = 0
            self._accum = 0.0
            self._prev_shot_remaining = 0
            return

        self.running = True
        self.shot_remaining = self._current_shot_length()
        self._prev_shot_remaining = self.shot_remaining
        self._last_tick = time.monotonic()
        self._accum = 0.0

    def update(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now

        if not self.running:
            return

        self._accum += dt
        dec = int(self._accum)
        if dec <= 0:
            return
        self._accum -= dec

        self.frame_remaining = max(0, self.frame_remaining - dec)
        self.shot_remaining = max(0, self.shot_remaining - dec)

        if self.frame_remaining <= FINAL_PHASE_SECONDS and self.shot_remaining > SHOT_CLOCK_FINAL_SECONDS:
            self.shot_remaining = SHOT_CLOCK_FINAL_SECONDS

        if self.frame_remaining == 0 or self.shot_remaining == 0:
            self.running = False
            self._accum = 0.0

    def shot_changed(self):
        cur = self.shot_remaining
        prev = self._prev_shot_remaining
        if cur != prev:
            self._prev_shot_remaining = cur
            return prev, cur
        return None


# ============================================================
# MAIN UI APPLICATION (pygame)
# ============================================================

class SpeedSnookerUI:
    def __init__(self) -> None:
        if AUDIO_ENABLED:
            pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)

        pygame.init()
        pygame.display.set_caption("Speed Snooker")

        self.screen = pygame.display.set_mode(RESOLUTION, pygame.FULLSCREEN)
        self.clock = pygame.time.Clock()

        self.fonts = {
            "title": pygame.font.SysFont(None, 120),
            "button": pygame.font.SysFont(None, 90),
            "timer": pygame.font.SysFont(None, 500),
            "shot": pygame.font.SysFont(None, 250),
            "hint": pygame.font.SysFont(None, 44),
        }

        self.state = "MENU"
        self.selected_index = 0
        self.frame: Optional[FrameController] = None

        self.buttons = self._build_menu_buttons()

        self.beep_short: Optional[pygame.mixer.Sound] = None
        self.beep_long: Optional[pygame.mixer.Sound] = None
        if AUDIO_ENABLED:
            try:
                self.beep_short = make_tone_sound(BEEP_FREQ_HZ, BEEP_SHORT_MS, BEEP_VOLUME)
                self.beep_long = make_tone_sound(BEEP_FREQ_HZ, BEEP_LONG_MS, BEEP_VOLUME)
            except Exception:
                self.beep_short = None
                self.beep_long = None

        self._start_fob_listener()

    def _build_menu_buttons(self):
        w, h = RESOLUTION
        btn_w, btn_h = 650, 130
        gap = 40
        total_h = (btn_h * len(GAME_OPTIONS)) + (gap * (len(GAME_OPTIONS) - 1))
        top = (h - total_h) // 2 + 80

        buttons = []
        for i, (label, seconds) in enumerate(GAME_OPTIONS):
            x = (w - btn_w) // 2
            y = top + i * (btn_h + gap)
            rect = pygame.Rect(x, y, btn_w, btn_h)
            buttons.append(Button(label, rect, seconds))
        return buttons

    def load_frame_paused(self, seconds: int) -> None:
        self.frame = FrameController(seconds)
        self.state = "FRAME"

    def back_to_menu(self) -> None:
        self.state = "MENU"
        self.selected_index = 0
        self.frame = None

    def _play_short(self) -> None:
        if self.beep_short:
            self.beep_short.play()

    def _play_long(self) -> None:
        if self.beep_long:
            self.beep_long.play()

    # -------------------------
    # FOB (evdev) integration
    # -------------------------

    def _find_fob_device_path(self) -> Optional[str]:
        """
        Returns a stable device path for the fob.

        Priority:
        1) FOB_DEVICE_PATH if set and exists (prefer /dev/input/by-id/...).
        2) Auto-discovery via evdev list_devices():
           - select devices that support KEY_VOLUMEUP
           - prefer device names matching FOB_NAME_HINTS
        """
        if FOB_DEVICE_PATH and os.path.exists(FOB_DEVICE_PATH):
            return FOB_DEVICE_PATH

        try:
            from evdev import InputDevice, list_devices, ecodes
        except Exception:
            return None

        candidates = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities().get(ecodes.EV_KEY, [])
                if ecodes.KEY_VOLUMEUP in caps:
                    candidates.append((path, dev.name))
            except Exception:
                continue

        if not candidates:
            return None

        hints = [h.lower() for h in FOB_NAME_HINTS if h]
        if hints:
            for path, name in candidates:
                n = (name or "").lower()
                if any(h in n for h in hints):
                    return path

        # Fallback: first device that advertises KEY_VOLUMEUP
        # (This may be a keyboard with volume keys; refine hints to avoid that.)
        return candidates[0][0]

    def _start_fob_listener(self) -> None:
        """
        Reads the fob directly using evdev and injects FOB_TRIGGER_EVENT into pygame.

        Behavior:
        - Auto-discovers the device
        - Reconnects if device path changes or disconnects
        - Triggers only on KEY_VOLUMEUP key-down (value == 1)

        Permissions:
        - Needs read access to /dev/input/*.
        - Quick path: run with sudo.
        """
        try:
            from evdev import InputDevice, ecodes
        except Exception:
            return

        def worker():
            while True:
                path = self._find_fob_device_path()
                if not path:
                    time.sleep(1.0)
                    continue

                try:
                    dev = InputDevice(path)
                except Exception:
                    time.sleep(1.0)
                    continue

                try:
                    for e in dev.read_loop():
                        if e.type == ecodes.EV_KEY and e.code == FOB_KEY_CODE and e.value == 1:
                            pygame.event.post(pygame.event.Event(FOB_TRIGGER_EVENT))
                except Exception:
                    # Device disconnected / permission changed / stream error. Retry discovery.
                    time.sleep(0.5)

        threading.Thread(target=worker, daemon=True).start()

    # -------------------------
    # Input handling
    # -------------------------

    def handle_event(self, event) -> None:
        if event.type == pygame.QUIT:
            raise SystemExit

        # Fob trigger injected from evdev thread
        if event.type == FOB_TRIGGER_EVENT:
            if self.state == "FRAME" and self.frame:
                self.frame.toggle_run()
            return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                raise SystemExit

            if self.state == "MENU":
                if event.key in (pygame.K_UP, pygame.K_w):
                    self.selected_index = (self.selected_index - 1) % len(self.buttons)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    self.selected_index = (self.selected_index + 1) % len(self.buttons)
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.load_frame_paused(self.buttons[self.selected_index].seconds)

            elif self.state == "FRAME":
                if event.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
                    self.back_to_menu()
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if self.frame:
                        self.frame.toggle_run()

    # -------------------------
    # Rendering
    # -------------------------

    def draw_menu(self) -> None:
        self.screen.fill(COLORS["bg"])

        draw_centered_text(
            self.screen,
            self.fonts["title"],
            "SPEED SNOOKER",
            (RESOLUTION[0] // 2, 160),
            COLORS["fg"],
        )

        for i, btn in enumerate(self.buttons):
            selected = (i == self.selected_index)
            border = COLORS["accent"] if selected else COLORS["dim"]
            fill = (22, 22, 22) if selected else COLORS["panel"]

            pygame.draw.rect(self.screen, fill, btn.rect, border_radius=18)
            pygame.draw.rect(self.screen, border, btn.rect, width=6, border_radius=18)

            draw_centered_text(
                self.screen,
                self.fonts["button"],
                btn.label,
                btn.rect.center,
                COLORS["fg"] if selected else COLORS["dim"],
            )

        draw_centered_text(
            self.screen,
            self.fonts["hint"],
            "UP/DOWN + ENTER   |   ESC QUIT",
            (RESOLUTION[0] // 2, RESOLUTION[1] - 70),
            COLORS["dim"],
        )

    def draw_frame(self) -> None:
        self.screen.fill(COLORS["bg"])

        frame_remaining = self.frame.frame_remaining if self.frame else 0
        shot_remaining = self.frame.shot_remaining if self.frame else 0
        running = self.frame.running if self.frame else False

        draw_centered_text(
            self.screen,
            self.fonts["title"],
            "FRAME TIMER",
            (RESOLUTION[0] // 2, 140),
            COLORS["fg"],
        )

        draw_centered_text(
            self.screen,
            self.fonts["timer"],
            fmt_time(frame_remaining),
            (RESOLUTION[0] // 2, RESOLUTION[1] // 2 - 40),
            COLORS["accent"] if frame_remaining > 0 else COLORS["fg"],
        )

        draw_centered_text(
            self.screen,
            self.fonts["hint"],
            "SHOT CLOCK",
            (RESOLUTION[0] // 2, RESOLUTION[1] // 2 + 150),
            COLORS["dim"],
        )

        shot_color = (
            COLORS["shot_critical"]
            if 1 <= shot_remaining <= 5
            else (COLORS["shot"] if shot_remaining > 0 else COLORS["dim"])
        )

        draw_centered_text(
            self.screen,
            self.fonts["shot"],
            f"{shot_remaining:02d}",
            (RESOLUTION[0] // 2, RESOLUTION[1] // 2 + 240),
            shot_color,
        )

        status = "RUNNING" if running else "PAUSED"
        draw_centered_text(
            self.screen,
            self.fonts["hint"],
            f"{status}   |   ENTER/FOB START/STOP   |   BACKSPACE MENU   |   ESC QUIT",
            (RESOLUTION[0] // 2, RESOLUTION[1] - 70),
            COLORS["dim"],
        )

    # -------------------------
    # Main loop
    # -------------------------

    def run(self) -> None:
        while True:
            for event in pygame.event.get():
                self.handle_event(event)

            if self.state == "FRAME" and self.frame:
                self.frame.update()
                changed = self.frame.shot_changed()
                if changed:
                    prev, cur = changed
                    if 1 <= cur <= 5:
                        self._play_short()
                    elif cur == 0 and prev > 0:
                        self._play_long()

            if self.state == "MENU":
                self.draw_menu()
            elif self.state == "FRAME":
                self.draw_frame()

            pygame.display.flip()
            self.clock.tick(FPS)


if __name__ == "__main__":
    try:
        SpeedSnookerUI().run()
    except SystemExit:
        pygame.quit()
        sys.exit(0)
