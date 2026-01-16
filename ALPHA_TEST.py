"""
Speed Snooker Timer UI (single-file baseline)

What this program does:
- Fullscreen 1920x1080 UI on a Raspberry Pi (or any desktop).
- Screen 1 (MENU): choose a frame duration (30, 20, 15 minutes, or 5:30).
- Screen 2 (FRAME): shows two timers:
    1) Frame timer (counts down only while a "shot run" is active)
    2) Shot clock (counts down from 15s, or 10s in the last 5 minutes of the frame)
- ENTER starts/stops a shot run:
    - When started: both timers count down together.
    - When shot clock hits 0: auto-pauses (frame timer stops too).
- Audio: in the last 5 seconds of the shot clock it plays 5 short beeps (5..1),
  then a long beep when it hits 0.
- Visual: shot clock turns red in its last 5 seconds.

Controls:
- MENU: Up/Down (or W/S), Enter selects
- FRAME: Enter starts/stops; Backspace/Delete returns to menu; Esc quits
"""

import sys
import time
import math
from array import array
from dataclasses import dataclass
from typing import Optional

import pygame


# ============================================================
# CONFIG SECTION
# - Screen settings
# - Game options
# - Timer rules
# - Audio parameters
# - UI colors
# ============================================================

# Fullscreen resolution
RESOLUTION = (1920, 1080)
FPS = 60

# Menu durations in seconds (label, seconds)
GAME_OPTIONS = [
    ("30 MINUTES", 30 * 60),
    ("20 MINUTES", 20 * 60),
    ("15 MINUTES", 15 * 60),
    ("5:30", 5 * 60 + 30),
]

# Shot clock rules:
# - During the last 5 minutes of the frame, the shot clock becomes 10 seconds.
FINAL_PHASE_SECONDS = 5 * 60
SHOT_CLOCK_NORMAL_SECONDS = 15
SHOT_CLOCK_FINAL_SECONDS = 10

# Audio configuration (beep generation is done in-code; no audio files required)
AUDIO_ENABLED = True
SAMPLE_RATE = 44100
BEEP_FREQ_HZ = 880
BEEP_SHORT_MS = 120
BEEP_LONG_MS = 3000
BEEP_VOLUME = 0.6

# Basic palette
COLORS = {
    "bg": (40, 120, 40),
    "fg": (240, 240, 240),
    "accent": (80, 160, 255),
    "dim": (120, 120, 120),
    "panel": (16, 16, 16),
    "shot": (255, 255, 255),
    "shot_critical": (220, 40, 40),  # shot clock turns red at 5..1
}


# ============================================================
# UTILITY FUNCTIONS
# - Formatting seconds -> MM:SS
# - Draw centered text
# - Generate beep sounds (sine wave buffer)
# ============================================================

def fmt_time(total_seconds: int) -> str:
    """
    Convert seconds into MM:SS.
    Used for the main frame timer display.
    """
    total_seconds = max(0, int(total_seconds))
    m = total_seconds // 60
    s = total_seconds % 60
    return f"{m:02d}:{s:02d}"


def draw_centered_text(surface, font, text, center, color):
    """
    Render text and blit it to the surface centered at (x, y).
    This is how all text is placed in the UI.
    """
    img = font.render(text, True, color)
    rect = img.get_rect(center=center)
    surface.blit(img, rect)


def make_tone_sound(freq_hz: int, duration_ms: int, volume: float) -> pygame.mixer.Sound:
    """
    Generate a mono sine tone and return it as a pygame Sound object.

    Why:
    - Avoid shipping audio files.
    - Keep beeps deterministic.

    Mechanism:
    - Create a 16-bit PCM buffer (array('h')).
    - Fill it with a sine wave at freq_hz.
    - Apply a small fade-in/out to avoid clicks.
    """
    n_samples = int(SAMPLE_RATE * (duration_ms / 1000.0))
    amp = int(32767 * max(0.0, min(1.0, volume)))
    buf = array("h")
    step = (2.0 * math.pi * freq_hz) / SAMPLE_RATE

    # Fade to reduce click noise at start/end
    fade = min(200, n_samples // 10)  # in samples
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
    """
    Represents one menu choice (label + rectangle + duration in seconds).
    """
    label: str
    rect: pygame.Rect
    seconds: int


# ============================================================
# TIMER / STATE LOGIC (NO UI HERE)
# - FrameController holds the timer state and rules
# - It is independent of pygame rendering code
# ============================================================

class FrameController:
    """
    Owns the timer state for the FRAME screen.

    Fields:
    - frame_remaining: seconds remaining in the frame
    - shot_remaining: seconds remaining in the current shot run
    - running: whether the shot run is active (both timers ticking)

    Timing strategy:
    - Use time.monotonic() + fractional accumulation
    - Decrement timers in whole seconds (stable and predictable)
    """

    def __init__(self, frame_total_seconds: int):
        # Remaining frame time in seconds
        self.frame_remaining = int(frame_total_seconds)

        # Shot clock remaining time in seconds (0 means not running)
        self.shot_remaining = 0

        # True only during an active "shot run"
        self.running = False

        # Time tracking for stable countdown
        self._last_tick = time.monotonic()
        self._accum = 0.0

        # Used to detect shot clock changes for triggering beeps
        self._prev_shot_remaining = 0

    def _current_shot_length(self) -> int:
        """
        Decide shot length based on remaining frame time:
        - last 5 minutes => 10 seconds
        - otherwise      => 15 seconds
        """
        if self.frame_remaining <= FINAL_PHASE_SECONDS:
            return SHOT_CLOCK_FINAL_SECONDS
        return SHOT_CLOCK_NORMAL_SECONDS

    def toggle_run(self) -> None:
        """
        SPACE behavior:
        - If paused: start a shot run (shot clock set to 15 or 10 depending on frame time).
        - If running: immediately pause (shot clock cleared to 0).
        """
        # If frame is over, refuse to start
        if self.frame_remaining <= 0:
            self.running = False
            self.shot_remaining = 0
            self._accum = 0.0
            self._prev_shot_remaining = 0
            return

        # If currently running -> pause and reset shot clock
        if self.running:
            self.running = False
            self.shot_remaining = 0
            self._accum = 0.0
            self._prev_shot_remaining = 0
            return

        # Start a new shot run
        self.running = True
        self.shot_remaining = self._current_shot_length()
        self._prev_shot_remaining = self.shot_remaining
        self._last_tick = time.monotonic()
        self._accum = 0.0

    def update(self) -> None:
        """
        Called every frame from the main loop.
        Only decrements timers when running == True.

        Uses fractional accumulation so countdown continues correctly at high FPS.
        """
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now

        if not self.running:
            return

        # Accumulate dt until we have at least one whole second to consume
        self._accum += dt
        dec = int(self._accum)
        if dec <= 0:
            return
        self._accum -= dec

        # Decrement both timers by whole seconds
        self.frame_remaining = max(0, self.frame_remaining - dec)
        self.shot_remaining = max(0, self.shot_remaining - dec)

        # If we crossed into final 5 minutes during a run, clamp the shot to 10 seconds.
        # This avoids a 15s shot continuing in the final phase.
        if self.frame_remaining <= FINAL_PHASE_SECONDS and self.shot_remaining > SHOT_CLOCK_FINAL_SECONDS:
            self.shot_remaining = SHOT_CLOCK_FINAL_SECONDS

        # Auto-stop when either timer hits 0
        if self.frame_remaining == 0 or self.shot_remaining == 0:
            self.running = False
            self._accum = 0.0

    def shot_changed(self):
        """
        Returns (prev, cur) once per shot_remaining change.
        Used to trigger beeps at 5..1 and the final long beep at 0.
        """
        cur = self.shot_remaining
        prev = self._prev_shot_remaining
        if cur != prev:
            self._prev_shot_remaining = cur
            return prev, cur
        return None


# ============================================================
# MAIN UI APPLICATION (pygame)
# - Initializes pygame + fonts + audio
# - Holds "MENU" and "FRAME" screens
# - Routes input and calls draw/update methods
# ============================================================

class SpeedSnookerUI:
    def __init__(self) -> None:
        # Initialize mixer before pygame.init() for best compatibility
        if AUDIO_ENABLED:
            pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)

        pygame.init()
        pygame.display.set_caption("Speed Snooker")

        # Fullscreen window
        self.screen = pygame.display.set_mode(RESOLUTION, pygame.FULLSCREEN)
        self.clock = pygame.time.Clock()

        # Fonts control the visual "size" of the clocks and headings
        self.fonts = {
            "title": pygame.font.SysFont(None, 120),
            "button": pygame.font.SysFont(None, 90),
            "timer": pygame.font.SysFont(None, 500),  # main frame clock size
            "shot": pygame.font.SysFont(None, 250),   # shot clock size
            "hint": pygame.font.SysFont(None, 44),
        }

        # Screen state machine
        self.state = "MENU"
        self.selected_index = 0
        self.frame: Optional[FrameController] = None

        # Precompute menu button rectangles
        self.buttons = self._build_menu_buttons()

        # Generate beep sounds (or disable if something fails)
        self.beep_short: Optional[pygame.mixer.Sound] = None
        self.beep_long: Optional[pygame.mixer.Sound] = None
        if AUDIO_ENABLED:
            try:
                self.beep_short = make_tone_sound(BEEP_FREQ_HZ, BEEP_SHORT_MS, BEEP_VOLUME)
                self.beep_long = make_tone_sound(BEEP_FREQ_HZ, BEEP_LONG_MS, BEEP_VOLUME)
            except Exception:
                self.beep_short = None
                self.beep_long = None

    def _build_menu_buttons(self):
        """
        Build centered vertical button stack.
        This controls the menu layout (button positions and sizes).
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
        """
        Transition from MENU to FRAME.
        Frame loads PAUSED; timers do not start until SPACE.
        """
        self.frame = FrameController(seconds)
        self.state = "FRAME"

    def back_to_menu(self) -> None:
        """
        Transition from FRAME back to MENU.
        """
        self.state = "MENU"
        self.selected_index = 0
        self.frame = None

    # ---- Audio wrappers ----
    def _play_short(self) -> None:
        if self.beep_short:
            self.beep_short.play()

    def _play_long(self) -> None:
        if self.beep_long:
            self.beep_long.play()

    def handle_event(self, event) -> None:
        """
        Central event handler.
        Routes input based on current state.
        """
        if event.type == pygame.QUIT:
            raise SystemExit

        if event.type == pygame.KEYDOWN:
            # Global quit
            if event.key == pygame.K_ESCAPE:
                raise SystemExit

            # MENU controls
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
                elif getattr(event, "scancode", None) == 128:  # fob start/stop
                    self.frame.toggle_run()

    def draw_menu(self) -> None:
        """
        Render the menu screen (title + selectable time buttons).
        """
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
        """
        Render the frame screen:
        - Title
        - Main frame clock (MM:SS)
        - Shot clock (SS)
        - Status/controls
        """
        self.screen.fill(COLORS["bg"])

        frame_remaining = self.frame.frame_remaining if self.frame else 0
        shot_remaining = self.frame.shot_remaining if self.frame else 0
        running = self.frame.running if self.frame else False

        # Title
        draw_centered_text(
            self.screen,
            self.fonts["title"],
            "FRAME TIMER",
            (RESOLUTION[0] // 2, 140),
            COLORS["fg"],
        )

        # Main frame timer (position controls where it appears)
        draw_centered_text(
            self.screen,
            self.fonts["timer"],
            fmt_time(frame_remaining),
            (RESOLUTION[0] // 2, RESOLUTION[1] // 2 - 40),
            COLORS["accent"] if frame_remaining > 0 else COLORS["fg"],
        )

        # Shot label
        draw_centered_text(
            self.screen,
            self.fonts["hint"],
            "SHOT CLOCK",
            (RESOLUTION[0] // 2, RESOLUTION[1] // 2 + 150),
            COLORS["dim"],
        )

        # Shot timer color logic: red for last 5 seconds
        shot_color = (
            COLORS["shot_critical"]
            if 1 <= shot_remaining <= 5
            else (COLORS["shot"] if shot_remaining > 0 else COLORS["dim"])
        )

        # Shot clock display
        draw_centered_text(
            self.screen,
            self.fonts["shot"],
            f"{shot_remaining:02d}",
            (RESOLUTION[0] // 2, RESOLUTION[1] // 2 + 240),
            shot_color,
        )

        # Status line
        status = "RUNNING" if running else "PAUSED"
        draw_centered_text(
            self.screen,
            self.fonts["hint"],
            f"{status}   |   FOB START/STOP   |   BACKSPACE MENU   |   ESC QUIT",
            (RESOLUTION[0] // 2, RESOLUTION[1] - 70),
            COLORS["dim"],
        )

    def run(self) -> None:
        """
        Main loop:
        - Process input events
        - Update timers
        - Trigger audio based on shot countdown changes
        - Draw current screen
        """
        while True:
            # Event handling
            for event in pygame.event.get():
                self.handle_event(event)

            # Timer update + audio triggering only on FRAME screen
            if self.state == "FRAME" and self.frame:
                self.frame.update()
                changed = self.frame.shot_changed()
                if changed:
                    prev, cur = changed
                    # Beep in last 5 seconds (5..1)
                    if 1 <= cur <= 5:
                        self._play_short()
                    # Long beep at 0 (end of shot)
                    elif cur == 0 and prev > 0:
                        self._play_long()

            # Render
            if self.state == "MENU":
                self.draw_menu()
            elif self.state == "FRAME":
                self.draw_frame()

            pygame.display.flip()
            self.clock.tick(FPS)


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        SpeedSnookerUI().run()
    except SystemExit:
        pygame.quit()
        sys.exit(0)
