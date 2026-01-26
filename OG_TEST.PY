import sys
import time
import math
from array import array
from dataclasses import dataclass
from typing import Optional

import pygame


# ---------- Config ----------
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

# Audio
AUDIO_ENABLED = True
SAMPLE_RATE = 44100
BEEP_FREQ_HZ = 880
BEEP_SHORT_MS = 120
BEEP_LONG_MS = 3000
BEEP_VOLUME = 0.6

COLORS = {
    "bg": (10, 10, 10),
    "fg": (240, 240, 240),
    "accent": (80, 160, 255),
    "dim": (120, 120, 120),
    "panel": (16, 16, 16),
    "shot": (255, 255, 255),
    "shot_critical": (220, 40, 40),  # red
}


# ---------- Utils ----------
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

    fade = min(200, n_samples // 10)  # samples
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


@dataclass
class Button:
    label: str
    rect: pygame.Rect
    seconds: int


# ---------- Timing Model ----------
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

        # Clamp to 10 if entering final phase mid-run
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


# ---------- App ----------
class SpeedSnookerUI:
    def __init__(self) -> None:
        self.audio_ok = False
        if AUDIO_ENABLED:
            pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)

        pygame.init()
        pygame.display.set_caption("Speed Snooker")

        self.screen = pygame.display.set_mode(RESOLUTION, pygame.FULLSCREEN)
        self.clock = pygame.time.Clock()

        self.fonts = {
            "title": pygame.font.SysFont(None, 120),
            "button": pygame.font.SysFont(None, 90),
            "timer": pygame.font.SysFont(None, 220),
            "shot": pygame.font.SysFont(None, 120),
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
                self.audio_ok = True
            except Exception:
                self.audio_ok = False
                self.beep_short = None
                self.beep_long = None

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

    def handle_event(self, event) -> None:
        if event.type == pygame.QUIT:
            raise SystemExit

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
                elif event.key == pygame.K_t and self.frame:
                    self.frame.toggle_run()

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
            f"{status}   |   SPACE START/STOP   |   BACKSPACE MENU   |   ESC QUIT",
            (RESOLUTION[0] // 2, RESOLUTION[1] - 70),
            COLORS["dim"],
        )

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
