import sys
import pygame

# Minimal key/scancode logger for Bluetooth fobs and media keys.
# Run fullscreen so it captures input like your main app.
# Press the fob button(s). Read the printed scancode/keyname.
# Exit with ESC.

RESOLUTION = (800, 450)
FPS = 60

def main():
    pygame.init()
    pygame.display.set_caption("Input Debug Logger")
    screen = pygame.display.set_mode(RESOLUTION)  # windowed is better for debugging
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 28)

    lines = []

    def add_line(s: str):
        nonlocal lines
        lines.append(s)
        lines = lines[-18:]  # keep last lines

    add_line("Press fob buttons. Watch console + on-screen log. ESC quits.")

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

                key = event.key
                name = pygame.key.name(key)
                sc = getattr(event, "scancode", None)
                uni = getattr(event, "unicode", "")
                mod = event.mod

                msg = f"KEYDOWN: key={key} name='{name}' scancode={sc} unicode='{uni}' mod={mod}"
                print(msg)
                add_line(msg)

            if event.type == pygame.KEYUP:
                key = event.key
                name = pygame.key.name(key)
                sc = getattr(event, "scancode", None)
                msg = f"KEYUP:   key={key} name='{name}' scancode={sc}"
                print(msg)
                add_line(msg)

        screen.fill((15, 15, 15))
        y = 10
        for s in lines:
            img = font.render(s, True, (230, 230, 230))
            screen.blit(img, (10, y))
            y += 24

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    sys.exit(0)

if __name__ == "__main__":
    main()
