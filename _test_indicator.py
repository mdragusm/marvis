import math
import random
import time

from marvis import indicator


def _demo() -> None:
    time.sleep(3)  # idle, on launch

    indicator.show()  # thinking
    time.sleep(3)
    indicator.hide()  # back to idle - triggers the lightbulb pop
    time.sleep(2)

    indicator.start_speaking()
    t0 = time.time()
    try:
        while time.time() - t0 < 12:
            t = time.time() - t0
            levels = [
                max(0.0, min(1.0, 0.5 + 0.5 * math.sin(t * 3 + i * 1.3) + random.uniform(-0.15, 0.15)))
                for i in range(5)
            ]
            indicator.set_levels(levels)
            time.sleep(0.05)
    finally:
        indicator.stop_speaking()
        time.sleep(0.3)


indicator.run(_demo)
