# ============================================================
# Imports
# - Standard library: system exit, timing, math, raw audio buffer helpers
# - Dataclasses/typing: simple structured data + type hints
# - pygame: windowing, input, rendering, audio output
# ============================================================
import sys
import time
import math
from array import array
from dataclasses import dataclass
from typing import Optional

import pygame


# ============================================================
# Config
# - Screen and FPS
# - Menu options (label, seconds)
# - Shot-clock rule changes (last 5 minutes)
# - Audio tone parameters (beeps generated in code)
# - UI colour palette
# ============================================================

# Fullscreen display size and target update rate
RESOLUTION = (1920, 1080)
FPS = 60

# Menu items: each tuple is ("label shown on button", duration_in_seconds)
GAME_OPTIONS = [
    ("30 MINUTES", 30 * 60),
    ("20 MINUTES", 20 * 60),
    ("15 MINUTES", 15 * 60),
    ("5:30", 5 * 60 + 30),
]

# Frame rule: when the frame timer is within the last 5 minutes,
# the shot clock uses a shorter duration (10s instead of 15s).
FINAL_PHASE_SECONDS = 5 * 60
SHOT_CLOCK_NORMAL_SECONDS = 15
SHOT_CLOCK_FINAL_SECONDS = 10

# Audio configuration: generate two beep sounds (short + long) at startup.
AUDIO_ENABLED = True
SAMPLE_RATE = 44100
BEEP_FREQ_HZ = 880
BEEP_SHORT_MS = 120
BEEP_LONG_MS = 3000
BEEP_VOLUME = 0.6

# Centralised colours (RGB tuples). Changing here changes the whole theme.
COLORS = {
    "bg": (10, 10, 10),             # background
    "fg": (240, 240, 240),          # main text (white)
    "accent": (80, 160, 255),       # highlight colour (main timer)
    "dim": (120, 120, 120),         # less important text
    "panel": (16, 16, 16),          # menu button fill
    "shot": (255, 255, 255),        # shot clock normal colour
    "shot_critical": (220, 40, 40), # shot clock last 5 seconds (red)
}


# ============================================================
# Utils
# - Time formatting
# - Text drawing helper
# - Tone generation (creates pygame Sound objects without audio files)
# ============================================================

def fmt_time(total_seconds: int) -> str:
    """Convert seconds -> 'MM:SS' for the main frame timer display."""
    total_seconds = max(0, int(total_seconds))
    m = total_seconds // 60
    s = total_seconds % 60
    return f"{m:02d}:{s:02d}"


def draw_centered_text(surface, font, text, center, color):
    """Render a string and blit it so its rect is centered at (x, y)."""
    img = font.render(text, True, color)
    rect = img.get_rect(center=center)
    surface.blit(img, rect)


def make_tone_sound(freq_hz: int, duration_ms: int, volume: float) -> pygame.mixer.Sound:
    """
    Generate a sine-wave tone as raw 16-bit PCM and wrap it as pygame Sound.

    - n_samples: number of audio samples for duration_ms at SAMPLE_RATE
    - amp: amplitude scaled by volume, clamped to [0..1]
    - fade: small fade in/out to reduce audible clicks
    """
    n_samples = int(SAMPLE_RATE * (duration_ms / 1000.0))
    amp = int(32767 * max(0.0, min(1.0, volume)))
    buf = array("h")  # signed 16-bit samples
    step = (2.0 * math.pi * freq_hz) / SAMPLE_RATE

    fade = min(200, n_samples // 10)  # fade length in samples
    for i in range(n_samples):
        v = math.sin(step * i)
        sample = int(amp * v)

        # Apply fade-in / fade-out envelope
        if fade > 0:
            if i < fade:
                sample = int(sample * (i / fade))
            elif i > n_samples - fade:
                sample = int(sample * ((n_samples - i) / fade))

        buf.append(sample)

    return pygame.mixer.Sound(buffer=buf.tobytes())


# ============================================================
# Data model: Button
# - Holds menu button label, its clickable rect, and its duration in seconds
# ============================================================

@dataclass
class Button:
    label: str
    rect: pygame.Rect
    seconds: int


# ============================================================
# Timing Model: FrameController
# - Pure logic (no rendering)
# - Manages:
#   - frame_remaining: main frame countdown
#   - shot_remaining: current shot countdown
#   - running: whether timers are currently ticking
# - Uses fractional time accumulation so countdown works at high FPS
# ============================================================

class FrameController:
    def __init__(self, frame_total_seconds: int):
        # Main frame time remaining
        self.frame_remaining = int(frame_total_seconds)

        # Shot clock time remaining (0 means not currently in a shot run)
        self.shot_remaining = 0

        # True while a shot run is active (both timers decrement)
        self.running = False

        # Timing internals for stable decrement
        self._last_tick = time.monotonic()
        self._accum = 0.0

        # Used to detect when shot_remaining changes (for beep triggers)
        self._prev_shot_remaining = 0

    def _current_shot_length(self) -> int:
        """Choose 10s shot clock in final phase, otherwise 15s."""
        if self.frame_remaining <= FINAL_PHASE_SECONDS:
            return SHOT_CLOCK_FINAL_SECONDS
        return SHOT_CLOCK_NORMAL_SECONDS

    def toggle_run(self) -> None:
        """
        Start/stop a shot run.
        - If frame is finished: force stopped.
        - If currently running: stop and clear shot clock.
        - If stopped: start, set shot clock to 15 or 10 depending on frame time.
        """
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
        """
        Called every frame.
        - If not running: do nothing.
        - Accumulate fractional dt.
        - When >= 1 second accumulated, decrement timers by whole seconds.
        """
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

        # Decrement both timers
        self.frame_remaining = max(0, self.frame_remaining - dec)
        self.shot_remaining = max(0, self.shot_remaining - dec)

        # If we enter final phase while a 15s shot is running, clamp it to 10s.
        if self.frame_remaining <= FINAL_PHASE_SECONDS and self.shot_remaining > SHOT_CLOCK_FINAL_SECONDS:
            self.shot_remaining = SHOT_CLOCK_FINAL_SECONDS

        # Auto-stop when either timer reaches 0
        if self.frame_remaining == 0 or self.shot_remaining == 0:
            self.running = False
            self._accum = 0.0

    def shot_changed(self):
        """
        Returns (prev, cur) when shot_remaining changes.
        Used by the UI to:
        - play short beeps at 5..1
        - play long beep at 0
        """
        cur = self.shot_remaining
        prev = self._prev_shot_remaining
        if cur != prev:
            self._prev_shot_remaining = cur
            return prev, cur
        return None


# ============================================================
# App: SpeedSnookerUI
# - Owns pygame window, fonts, menu layout, input handling, drawing
# - Switches between states: "MENU" and "FRAME"
# - Calls FrameController for timing logic
# ============================================================

class SpeedSnookerUI:
    def __init__(self) -> None:
        # Pre-init mixer to control sample format before pygame.init()
        self.audio_ok = False
        if AUDIO_ENABLED:
            pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)

        pygame.init()
        pygame.display.set_caption("Speed Snooker")

        # Fullscreen surface and FPS limiter clock
        self.screen = pygame.display.set_mode(RESOLUTION, pygame.FULLSCREEN)
        self.clock = pygame.time.Clock()

        # Font sizes define the visual size of text/timers
        self.fonts = {
            "title": pygame.font.SysFont(None, 120),
            "button": pygame.font.SysFont(None, 90),
            "timer": pygame.font.SysFont(None, 220),
            "shot": pygame.font.SysFont(None, 120),
            "hint": pygame.font.SysFont(None, 44),
        }

        # UI state: start on menu
        self.state = "MENU"
        self.selected_index = 0
        self.frame: Optional[FrameController] = None

        # Build menu button rectangles once
        self.buttons = self._build_menu_buttons()

        # Generate beep sounds (short = countdown, long = end)
        self.beep_short: Optional[pygame.mixer.Sound] = None
        self.beep_long: Optional[pygame.mixer.Sound] = None
        if AUDIO_ENABLED:
            try:
                self.beep_short = make_tone_sound(BEEP_FREQ_HZ, BEEP_SHORT_MS, BEEP_VOLUME)
                self.beep_long = make_tone_sound(BEEP_FREQ_HZ, BEEP_LONG_MS, BEEP_VOLUME)
                self.audio_ok = True
            except Exception:
                # If audio init fails, keep running without sound.
                self.audio_ok = False
                self.beep_short = None
                self.beep_long = None

    def _build_menu_buttons(self):
        """
        Create menu buttons centered vertically.
        Returns a list[Button] with rect positions and duration values.
        """
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
        """Switch to FRAME screen with a new FrameController, initially paused."""
        self.frame = FrameController(seconds)
        self.state = "FRAME"

    def back_to_menu(self) -> None:
        """Return to MENU screen and clear current frame state."""
        self.state = "MENU"
        self.selected_index = 0
        self.frame = None

    def _play_short(self) -> None:
        """Play a short beep (used for 5..1 countdown)."""
        if self.beep_short:
            self.beep_short.play()

    def _play_long(self) -> None:
        """Play a long beep (used when shot clock hits 0)."""
        if self.beep_long:
            self.beep_long.play()

    def handle_event(self, event) -> None:
        """
        Handle pygame events:
        - ESC quits
        - MENU: navigate and select
        - FRAME: back to menu, or toggle timers via key 't'
        """
        if event.type == pygame.QUIT:
            raise SystemExit

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                raise SystemExit

            # MENU navigation + selection
            if self.state == "MENU":
                if event.key in (pygame.K_UP, pygame.K_w):
                    self.selected_index = (self.selected_index - 1) % len(self.buttons)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    self.selected_index = (self.selected_index + 1) % len(self.buttons)
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.load_frame_paused(self.buttons[self.selected_index].seconds)

            # FRAME controls
            elif self.state == "FRAME":
                if event.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
                    self.back_to_menu()
                # Toggle run on key 't' (assumes OS remaps fob -> 't' or you press 't')
                elif event.key == pygame.K_t and self.frame:
                    self.frame.toggle_run()

    def draw_menu(self) -> None:
        """Render MENU screen: title + selectable duration buttons + footer hint."""
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

            # Button styling depends on whether it's selected
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
        """Render FRAME screen: frame timer, shot clock, and status line."""
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

        # Main frame timer (MM:SS)
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

        # Shot clock turns red at 5..1 seconds
        shot_color = (
            COLORS["shot_critical"]
            if 1 <= shot_remaining <= 5
            else (COLORS["shot"] if shot_remaining > 0 else COLORS["dim"])
        )

        # Shot clock display (SS)
        draw_centered_text(
            self.screen,
            self.fonts["shot"],
            f"{shot_remaining:02d}",
            (RESOLUTION[0] // 2, RESOLUTION[1] // 2 + 240),
            shot_color,
        )

        # Footer status and controls hint (string is just UI text)
        status = "RUNNING" if running else "PAUSED"
        draw_centered_text(
            self.screen,
            self.fonts["hint"],
            f"{status}   |   SPACE START/STOP   |   BACKSPACE MENU   |   ESC QUIT",
            (RESOLUTION[0] // 2, RESOLUTION[1] - 70),
            COLORS["dim"],
        )

    def run(self) -> None:
        """
        Main loop:
        - Handle events
        - Update timers
        - Trigger beeps based on shot clock changes
        - Draw current screen
        - Flip frame buffer and limit FPS
        """
        while True:
            for event in pygame.event.get():
                self.handle_event(event)

            # Update timers + beep logic only while in FRAME
            if self.state == "FRAME" and self.frame:
                self.frame.update()
                changed = self.frame.shot_changed()
                if changed:
                    prev, cur = changed
                    # Short beep at 5..1
                    if 1 <= cur <= 5:
                        self._play_short()
                    # Long beep at 0 (shot ended)
                    elif cur == 0 and prev > 0:
                        self._play_long()

            # Draw the current UI state
            if self.state == "MENU":
                self.draw_menu()
            elif self.state == "FRAME":
                self.draw_frame()

            pygame.display.flip()
            self.clock.tick(FPS)


# ============================================================
# Program entry point
# - Create the UI and run until user exits
# ============================================================

if __name__ == "__main__":
    try:
        SpeedSnookerUI().run()
    except SystemExit:
        pygame.quit()
        sys.exit(0)
