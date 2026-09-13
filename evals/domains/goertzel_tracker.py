"""Goertzel frequency tracking domain implementations for cold-start convergence evaluation.

Domain B Challenge:
- Closed-loop frequency tracker under real sinusoidal excitation u[n] = cos(2*pi*f0*n*Ts).
- Physical pipeline delay D = 2 samples between phase differencer and oscillator update.
- Real sinusoidal excitation introduces a counter-rotating (-f0) component causing 2*f0 phase ripple.
- Naive implementation sets high loop bandwidth alpha = 0.50 assuming zero delay.
  Under D = 2, loop phase margin collapses, inducing catastrophic limit cycles / loss-of-lock.
- Compensated implementation tunes loop bandwidth to stable bounds (e.g. alpha = 0.02)
  and handles pipeline delay gracefully.
"""
import cmath
import math
from typing import Sequence


class NaiveGoertzelTracker:
    """Defective Goertzel frequency tracker with uncompensated high loop bandwidth."""

    def __init__(self, f0: float = 100.0, Fs: float = 5000.0, alpha: float = 0.50, delay: int = 2, c: float = 0.10):
        self.f0 = f0
        self.Fs = Fs
        self.Ts = 1.0 / Fs
        self.alpha = alpha
        self.delay = delay
        self.c = c

        self.omega0 = 2.0 * math.pi * f0
        self.dtheta_nom = self.omega0 * self.Ts
        # Delay buffer
        self.buf = [self.dtheta_nom] * (self.delay + 1)

        # Goertzel state
        self.x = complex(0.5 * (self.c / (1.0 - (1.0 - self.c))), 0.0)
        self.prev_angle = cmath.phase(self.x) - self.dtheta_nom
        self.curr_angle = cmath.phase(self.x)

    def step(self, u_sample: float) -> float:
        # Phase differencer
        diff = self.curr_angle - self.prev_angle
        # Unwrapped into [-pi, pi)
        diff = (diff + math.pi) % (2.0 * math.pi) - math.pi

        # 1st-order low-pass loop filter
        dtheta_f = (1.0 - self.alpha) * self.buf[0] + self.alpha * diff

        # Shift pipeline delay buffer
        self.buf = [dtheta_f] + self.buf[:-1]
        dtheta_applied = self.buf[self.delay]

        # Resonator update
        rot = cmath.rect(1.0, dtheta_applied)
        self.x = self.c * u_sample + (1.0 - self.c) * rot * self.x

        self.prev_angle = self.curr_angle
        self.curr_angle = cmath.phase(self.x)

        return float(dtheta_f / (2.0 * math.pi * self.Ts))

    def track_stream(self, samples: Sequence[float]) -> list[float]:
        return [self.step(u) for u in samples]


class CompensatedGoertzelTracker:
    """Remediated Goertzel frequency tracker with delay-compensated stable bandwidth."""

    def __init__(self, f0: float = 100.0, Fs: float = 5000.0, alpha: float = 0.02, delay: int = 2, c: float = 0.10):
        self.f0 = f0
        self.Fs = Fs
        self.Ts = 1.0 / Fs
        # Stable bandwidth for delay D >= 2
        self.alpha = alpha
        self.delay = delay
        self.c = c

        self.omega0 = 2.0 * math.pi * f0
        self.dtheta_nom = self.omega0 * self.Ts
        self.buf = [self.dtheta_nom] * (self.delay + 1)

        self.x = complex(0.5 * (self.c / (1.0 - (1.0 - self.c))), 0.0)
        self.prev_angle = cmath.phase(self.x) - self.dtheta_nom
        self.curr_angle = cmath.phase(self.x)

    def step(self, u_sample: float) -> float:
        diff = self.curr_angle - self.prev_angle
        diff = (diff + math.pi) % (2.0 * math.pi) - math.pi

        dtheta_f = (1.0 - self.alpha) * self.buf[0] + self.alpha * diff
        self.buf = [dtheta_f] + self.buf[:-1]
        dtheta_applied = self.buf[self.delay]

        rot = cmath.rect(1.0, dtheta_applied)
        self.x = self.c * u_sample + (1.0 - self.c) * rot * self.x

        self.prev_angle = self.curr_angle
        self.curr_angle = cmath.phase(self.x)

        return float(dtheta_f / (2.0 * math.pi * self.Ts))

    def track_stream(self, samples: Sequence[float]) -> list[float]:
        return [self.step(u) for u in samples]
