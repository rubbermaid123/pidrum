#!/usr/bin/env python3
# PiDrum - batterie d'apprentissage pour jeune enfant
# Kit de batterie Guitar Hero (PS3) + Raspberry Pi 4 + TV
#
# Aucun texte a l'ecran : tout passe par les couleurs, les sons et les animations.
#
# Modes (appui LONG sur START du kit, ~1.5 s, pour passer au suivant) :
#   1. Jeu libre  : chaque pad joue son son et s'illumine
#   2. Copie-moi  : l'ecran montre une sequence, l'enfant la reproduit
#   3. Le beat    : frapper le pad indique, au rythme du metronome
#
# Clavier (tests / parent) : 1-5 = pads, espace = pedale, Entree = START,
#                            Echap = quitter

import argparse
import collections
import json
import math
import os
import random
import select
import sys
import threading
import time

# Doit etre defini avant l'import de pygame pour masquer le message d'accueil
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

from sounds import build_sounds  # noqa: E402

# ---------------------------------------------------------------------------
# Reglages
# ---------------------------------------------------------------------------

FPS = 60
# Rendu logiciel dans cette taille, agrandi par le GPU au plein ecran (pygame.SCALED).
# Rendre en natif 1440p sur le Pi 4 tombe a ~14 FPS ; 720p laisse de la marge.
RENDER_SIZE = (1280, 720)
AUDIO_RATE = 48000          # frequence native HDMI et jack du Pi 4 (pas de reechantillonnage)
AUDIO_OUTPUT = "auto"       # "auto" (HDMI si l'ecran a du son, sinon jack), "hdmi" ou "jack"
                            # la variable d'environnement AUDIODEV reste prioritaire
DISPLAY_WAIT = 30.0         # (s) au boot, attente que l'ecran (kmsdrm) soit pret
AUDIO_BUFFER = 256          # echantillons ; monter a 512 si craquements
BPM = 70                    # tempo du mode "Le beat"
BEAT_WINDOW = 0.25          # tolerance (s) autour du temps, genereuse pour 3 ans
BEAT_GOAL = 8               # bonnes frappes avant la fanfare et le niveau suivant
JUDGE_OFFSET = 0.0          # (s) compense la latence TV si les frappes semblent en retard
HOLD_TO_SWITCH = 1.5        # duree d'appui sur START pour changer de mode
DEBOUNCE = 0.03             # ignore les doubles declenchements d'un meme pad
KICK_IN_GAMES = False       # inclure la pedale dans Copie-moi / Le beat
SIMON_MAX_LEN = 4           # longueur max des sequences de Copie-moi
SIMON_HINT_DELAY = 2.5      # (s) avant d'indiquer le pad attendu (sequences >= 2)
IDLE_HINT_AFTER = 20.0      # (s) sans frappe avant d'inviter l'enfant a jouer
IDLE_HINT_EVERY = 3.0       # (s) entre deux invitations
COPY_STEP = 0.9             # (s) entre deux pads montres dans Copie-moi
COPY_START_DELAY = 1.0      # (s) avant la premiere demonstration
COPY_REPLAY_AFTER = 12.0    # (s) d'inactivite avant de rejouer la sequence
COPY_WINS_TO_GROW = 3       # reussites avant d'allonger la sequence
COPY_ERRORS_TO_SHRINK = 5   # erreurs dans une manche avant de la raccourcir
COPY_PAUSE_WIN = 2.2        # (s) de fete apres une sequence reussie
BEAT_LEAD_IN = 2            # temps de metronome avant le premier temps juge
BEAT_REST_AFTER_GOAL = 4    # temps de pause apres la fanfare
MAX_PARTICLES = 500
RECONNECT_EVERY = 2.0       # (s) entre deux recherches du kit

BG = (16, 16, 32)
DARK_FILL = (34, 34, 52)
WHITE = (255, 255, 255)

# Disposition a l'ecran calquee sur le kit vu par l'enfant
PADS = {
    "yellow": {"color": (250, 205, 30), "pos": (0.22, 0.27), "shape": "cymbal", "sound": "hihat"},
    "orange": {"color": (255, 125, 20), "pos": (0.78, 0.27), "shape": "cymbal", "sound": "crash"},
    "red":    {"color": (225, 40, 55),  "pos": (0.20, 0.66), "shape": "drum",   "sound": "snare"},
    "blue":   {"color": (45, 115, 240), "pos": (0.50, 0.58), "shape": "drum",   "sound": "tom_hi"},
    "green":  {"color": (40, 195, 85),  "pos": (0.80, 0.66), "shape": "drum",   "sound": "tom_lo"},
    "kick":   {"color": (170, 95, 230), "pos": (0.50, 0.93), "shape": "bar",    "sound": "kick"},
}
GAME_PADS = ["red", "yellow", "blue", "orange", "green"]

KEYBOARD = {
    pygame.K_1: "red", pygame.K_2: "yellow", pygame.K_3: "blue",
    pygame.K_4: "orange", pygame.K_5: "green", pygame.K_SPACE: "kick",
    pygame.K_RETURN: "start", pygame.K_TAB: "select",
}

HERE = os.path.dirname(os.path.abspath(__file__))
PADS_FILE = os.path.join(HERE, "pads.json")


def lerp(a, b, p):
    p = max(0.0, min(1.0, p))
    return tuple(int(a[i] + (b[i] - a[i]) * p) for i in range(3))


def star_points(cx, cy, r, rot):
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.45
        a = rot + i * math.pi / 5 - math.pi / 2
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


# ---------------------------------------------------------------------------
# Entree : kit de batterie via evdev (reconnexion automatique)
# ---------------------------------------------------------------------------

class KeyDecoder:
    """Traduit les evenements evdev bruts en ("down"|"up", role).

    Dans pads.json, un role est soit un code (int), soit une combinaison
    [code, code] quand le kit envoie couleur + modificateur pour un meme coup.
    Les roles sont evalues a chaque trame (SYN_REPORT) : si une combinaison et
    un de ses sous-ensembles sont satisfaits, seule la plus specifique part.
    """
    EV_SYN, EV_KEY = 0, 1

    def __init__(self, keys):
        self.roles = {}
        for role, code in keys.items():
            codes = code if isinstance(code, list) else [code]
            self.roles[role] = frozenset(int(c) for c in codes)
        self.watched = frozenset().union(*self.roles.values()) if self.roles else frozenset()
        self.held = set()
        self.frame_down = set()
        self.frame_up = set()
        self.active = set()

    def feed(self, ev_type, code, value):
        """Retourne la liste des evenements produits par cet evenement evdev."""
        if ev_type == self.EV_KEY and code in self.watched:
            if value == 1:
                self.held.add(code)
                self.frame_down.add(code)
            elif value == 0:
                self.held.discard(code)
                self.frame_up.add(code)
            return []
        if ev_type == self.EV_SYN and code == 0:
            return self._frame()
        return []

    def _frame(self):
        out = []
        for role in sorted(self.active):
            if not self.roles[role] <= self.held:
                self.active.discard(role)
                out.append(("up", role))
        if self.frame_down:
            seen = self.held | self.frame_down
            hits = [r for r, c in self.roles.items() if c <= seen and c & self.frame_down]
            hits = [r for r in hits if not any(self.roles[r] < self.roles[o] for o in hits)]
            for role in sorted(hits):
                if role not in self.active:
                    out.append(("down", role))
                    if self.roles[role] <= self.held:
                        self.active.add(role)
                    else:
                        out.append(("up", role))
        self.frame_down.clear()
        self.frame_up.clear()
        return out


class EvdevInput:
    """Lit le kit dans un thread dedie et appelle callback(kind, role, t).

    Le thread bloque sur select() : le son part ~1 ms apres la frappe, meme si
    le thread principal est bloque dans display.flip() (attente vsync kmsdrm).
    """

    def __init__(self, cfg, callback):
        self.name = cfg["device_name"]
        self.decoder = KeyDecoder(cfg["keys"])
        self.callback = callback
        self.dev = None
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="kit", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.running = False

    def _open(self):
        import evdev
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except OSError:
                continue
            if d.name == self.name:
                print("Kit connecte :", path, d.name)
                return d
            d.close()
        return None

    def _close(self):
        try:
            self.dev.close()
        except Exception:
            pass
        self.dev = None
        self.decoder = KeyDecoder({r: sorted(c) for r, c in self.decoder.roles.items()})

    def _loop(self):
        warned = False
        while self.running:
            if self.dev is None:
                self.dev = self._open()
                if self.dev is None:
                    if not warned:
                        print("Kit introuvable (%s), nouvel essai toutes les %g s"
                              % (self.name, RECONNECT_EVERY))
                        warned = True
                    time.sleep(RECONNECT_EVERY)
                    continue
                warned = False
            try:
                ready, _, _ = select.select([self.dev.fd], [], [], 0.5)
                if not ready:
                    continue
                for ev in self.dev.read():
                    for kind, role in self.decoder.feed(ev.type, ev.code, ev.value):
                        self.callback(kind, role, time.monotonic())
            except BlockingIOError:
                continue
            except (OSError, ValueError):
                print("Kit deconnecte")
                self._close()
                time.sleep(RECONNECT_EVERY)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

class Mode:
    dots = 0

    def enter(self, app, t):
        pass

    def on_hit(self, app, pad, t):
        pass

    def update(self, app, t):
        pass

    def draw(self, app, surf, t):
        pass


class FreePlay(Mode):
    dots = 1

    def enter(self, app, t):
        self.next_invite = 0.0

    def update(self, app, t):
        # Apres un moment sans frappe, un pad "appelle" doucement l'enfant
        if t - app.last_hit > IDLE_HINT_AFTER and t >= self.next_invite:
            app.ring(random.choice(GAME_PADS), t, strong=False)
            self.next_invite = t + IDLE_HINT_EVERY


class CopyMe(Mode):
    dots = 2
    STEP = COPY_STEP

    def enter(self, app, t):
        self.length = 1
        self.wins = 0
        self.new_round(t + COPY_START_DELAY)

    def new_round(self, start_t):
        pool = app_target_pads()
        seq = []
        for _ in range(self.length):
            choices = [p for p in pool if not seq or p != seq[-1]]
            seq.append(random.choice(choices))
        self.seq = seq
        self.start_show(start_t)

    def start_show(self, start_t):
        self.idx = 0
        self.errors = 0
        self.state = "show"
        self.show_t0 = start_t
        self.shown = 0

    def update(self, app, t):
        if self.state == "show":
            while self.shown < len(self.seq) and t >= self.show_t0 + self.shown * self.STEP:
                app.demo(self.seq[self.shown], t)
                self.shown += 1
            if t >= self.show_t0 + len(self.seq) * self.STEP:
                self.state = "wait"
                self.wait_t0 = t
        elif self.state == "wait":
            # L'enfant ne fait rien : on rejoue la sequence
            if t - self.wait_t0 > COPY_REPLAY_AFTER:
                self.start_show(t + 0.5)
        elif self.state == "pause":
            if t >= self.pause_until:
                self.new_round(t)

    def on_hit(self, app, pad, t):
        if self.state != "wait":
            return
        if pad == self.seq[self.idx]:
            self.idx += 1
            self.wait_t0 = t
            app.reward(pad, t)
            if self.idx >= len(self.seq):
                app.celebrate(t)
                self.wins += 1
                if self.wins >= COPY_WINS_TO_GROW and self.length < SIMON_MAX_LEN:
                    self.length += 1
                    self.wins = 0
                self.state = "pause"
                self.pause_until = t + COPY_PAUSE_WIN
        else:
            # Aucun son negatif : on montre simplement le bon pad
            self.errors += 1
            self.wait_t0 = t - SIMON_HINT_DELAY
            if self.errors >= COPY_ERRORS_TO_SHRINK and self.length > 1:
                self.length -= 1
                self.wins = 0
                self.state = "pause"
                self.pause_until = t + 1.0

    def draw(self, app, surf, t):
        if self.state == "wait":
            if self.length == 1 or t - self.wait_t0 >= SIMON_HINT_DELAY:
                app.draw_target(surf, self.seq[self.idx], t)


class Beat(Mode):
    dots = 3
    PATTERNS = [
        ["red"], ["blue"], ["green"], ["yellow"], ["orange"],
        ["red", "blue"], ["red", "green"], ["yellow", "red"],
        ["red", "blue", "green", "blue"],
    ]

    def enter(self, app, t):
        self.patterns = list(self.PATTERNS)
        if KICK_IN_GAMES:
            self.patterns[1:1] = [["kick"], ["kick", "red"]]
        self.period = 60.0 / BPM
        self.level = 0
        self.restart(t + BEAT_LEAD_IN * self.period)

    def restart(self, t0):
        self.t0 = t0
        self.good = 0
        self.hit_beats = set()
        self.last_tick = -1
        self.last_beat_t = -10.0

    def target(self, n):
        p = self.patterns[self.level]
        return p[n % len(p)]

    def update(self, app, t):
        n = math.floor((t - self.t0) / self.period)
        if n > self.last_tick and n >= 0:
            app.play("tick_accent" if n % 4 == 0 else "tick")
            self.last_beat_t = self.t0 + n * self.period
            self.last_tick = n

    def on_hit(self, app, pad, t):
        tj = t - JUDGE_OFFSET
        n = int(round((tj - self.t0) / self.period))
        if n < 0 or n in self.hit_beats:
            return
        if abs(tj - (self.t0 + n * self.period)) <= BEAT_WINDOW and self.target(n) == pad:
            self.hit_beats.add(n)
            self.good += 1
            app.reward(pad, t)
            if self.good >= BEAT_GOAL:
                app.celebrate(t)
                self.level = (self.level + 1) % len(self.patterns)
                self.restart(self.t0 + (n + BEAT_REST_AFTER_GOAL) * self.period)

    def draw(self, app, surf, t):
        w, h = surf.get_size()
        # Metronome : un rond qui "saute" a chaque temps
        cx, cy = int(w * 0.5), int(h * 0.22)
        base = h * 0.05
        pop = math.exp(-max(0.0, t - self.last_beat_t) / 0.12)
        pygame.draw.circle(surf, lerp(DARK_FILL, WHITE, 0.25 + 0.75 * pop), (cx, cy), int(base * (1 + 0.5 * pop)))
        # Progression : BEAT_GOAL petits ronds en haut
        gap = h * 0.045
        x0 = cx - gap * (BEAT_GOAL - 1) / 2
        for i in range(BEAT_GOAL):
            col = (250, 215, 60) if i < self.good else DARK_FILL
            pygame.draw.circle(surf, col, (int(x0 + i * gap), int(h * 0.07)), int(h * 0.015))
        # Cercles d'approche : se referment sur le pad exactement sur le temps
        n_next = max(0, math.floor((t - self.t0) / self.period) + 1)
        for k in (n_next - 1, n_next, n_next + 1):
            if k < 0 or k in self.hit_beats:
                continue
            dt = (self.t0 + k * self.period) - t
            if -0.12 <= dt <= self.period:
                app.draw_approach(surf, self.target(k), max(0.0, dt) / self.period)


def app_target_pads():
    return GAME_PADS + (["kick"] if KICK_IN_GAMES else [])


# ---------------------------------------------------------------------------
# Sorties audio et video
# ---------------------------------------------------------------------------

def hdmi_audio_cards(asound="/proc/asound"):
    """Cartes ALSA HDMI dont l'ecran accepte l'audio (ELD avec au moins un format)."""
    found = []
    try:
        entries = sorted(os.listdir(asound))
    except OSError:
        return found
    for entry in entries:
        if not entry.startswith("card"):
            continue
        base = os.path.join(asound, entry)
        try:
            eld_files = [f for f in os.listdir(base) if f.startswith("eld#")]
            with open(os.path.join(base, "id")) as f:
                card_id = f.read().strip()
        except OSError:
            continue
        for eld in eld_files:
            info = {}
            try:
                with open(os.path.join(base, eld)) as f:
                    for line in f:
                        parts = line.split(None, 1)
                        if parts:
                            info[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
            except OSError:
                continue
            try:
                sads = int(info.get("sad_count", "0"))
            except ValueError:
                sads = 0
            if sads > 0:
                found.append(card_id)
                break
    return found


def pick_audio_device(output=AUDIO_OUTPUT, asound="/proc/asound"):
    """Retourne la valeur AUDIODEV a utiliser, ou None pour le defaut ALSA."""
    try:
        cards = [e for e in sorted(os.listdir(asound)) if e.startswith("card")]
        ids = []
        for c in cards:
            with open(os.path.join(asound, c, "id")) as f:
                ids.append(f.read().strip())
    except OSError:
        return None
    jack = "Headphones" if "Headphones" in ids else None
    hdmi = hdmi_audio_cards(asound)
    if output == "jack":
        card = jack
    elif output == "hdmi":
        card = hdmi[0] if hdmi else None
    else:
        card = hdmi[0] if hdmi else jack
    return "plughw:CARD=%s,DEV=0" % card if card else None


def open_display(window):
    if window:
        return pygame.display.set_mode(RENDER_SIZE)
    # Au boot, le pilote vc4 peut ne pas etre pret quand le service demarre
    deadline = time.monotonic() + DISPLAY_WAIT
    warned = False
    while True:
        try:
            return pygame.display.set_mode(RENDER_SIZE, pygame.FULLSCREEN | pygame.SCALED)
        except pygame.error as exc:
            if time.monotonic() > deadline:
                raise
            if not warned:
                print("Ecran pas encore pret (%s), nouvel essai..." % exc)
                warned = True
            pygame.display.quit()
            time.sleep(1.0)
            pygame.display.init()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class App:
    def __init__(self, window=False, use_kit=True):
        # Pas de pygame.init() : il ouvrirait l'audio avant le choix de la sortie
        pygame.display.init()
        self.screen = open_display(window)
        pygame.display.set_caption("PiDrum")
        pygame.mouse.set_visible(False)
        self.layout(*self.screen.get_size())

        self.sounds = {}
        try:
            if "AUDIODEV" not in os.environ:
                dev = pick_audio_device()
                if dev:
                    os.environ["AUDIODEV"] = dev
            pygame.mixer.init(AUDIO_RATE, -16, 2, AUDIO_BUFFER)
            init = pygame.mixer.get_init()
            pygame.mixer.set_num_channels(32)
            self.sounds = build_sounds(pygame, init)
            print("Audio : %d Hz, format %d, %d canaux, sortie %s"
                  % (init + (os.environ.get("AUDIODEV", "default"),)))
        except Exception as exc:
            print("Audio indisponible (%s) : verifier AUDIODEV, voir aplay -L" % exc)

        now = time.monotonic()
        # Frappes du kit : le thread "kit" joue le son puis depose ici le reste
        self.strike_lock = threading.Lock()
        self.kit_queue = collections.deque()
        self.glow = {p: 0.0 for p in PADS}
        self.last_down = {p: 0.0 for p in PADS}
        self.effects = []      # anneaux
        self.particles = []    # eclats et etoiles
        self.kick_flash = 0.0
        self.last_hit = now
        self.start_down_at = None
        self.banner_until = 0.0
        self.modes = [FreePlay(), CopyMe(), Beat()]
        self.mode_idx = 0
        self.mode = self.modes[0]
        self.mode.enter(self, now)
        self.last_t = now
        self.running = True

        self.inp = None
        if use_kit:
            if os.path.exists(PADS_FILE):
                with open(PADS_FILE) as f:
                    self.inp = EvdevInput(json.load(f), self.kit_event)
                # Le thread kit doit pouvoir reprendre la main vite pendant le rendu
                sys.setswitchinterval(0.001)
                self.inp.start()
            else:
                print("pads.json absent : lancer map_pads.py. Clavier seulement.")

    def reset_clock(self, t):
        # Pour les tests : repart d'un temps synthetique
        self.last_t = t
        self.last_hit = t
        self.mode.enter(self, t)

    # --- geometrie --------------------------------------------------------

    def layout(self, w, h):
        self.w, self.h = w, h
        self.geo = {}
        for name, p in PADS.items():
            cx, cy = int(p["pos"][0] * w), int(p["pos"][1] * h)
            if p["shape"] == "drum":
                rx = ry = int(0.15 * h)
            elif p["shape"] == "cymbal":
                rx, ry = int(0.22 * h), int(0.085 * h)
            else:
                rx, ry = int(0.18 * w), int(0.03 * h)
            self.geo[name] = (cx, cy, rx, ry)

    # --- audio ------------------------------------------------------------

    def play(self, name, volume=1.0):
        s = self.sounds.get(name)
        if s is not None:
            ch = s.play()
            if ch is not None:
                ch.set_volume(volume)

    # --- effets -----------------------------------------------------------

    def ring(self, pad, t, strong=True):
        cx, cy, rx, ry = self.geo[pad]
        r = max(rx, ry)
        self.effects.append({
            "c": (cx, cy), "r0": r, "r1": r * (2.0 if strong else 1.5),
            "t0": t, "dur": 0.5 if strong else 0.9,
            "col": PADS[pad]["color"], "w": max(3, int(self.h * (0.012 if strong else 0.006))),
        })

    def burst(self, pad, n=12, stars=False):
        cx, cy, rx, ry = self.geo[pad]
        for _ in range(n):
            a = random.uniform(0, 2 * math.pi)
            sp = random.uniform(0.35, 0.8) * self.h
            self.particles.append({
                "x": cx + math.cos(a) * rx * 0.6, "y": cy + math.sin(a) * ry * 0.6,
                "vx": math.cos(a) * sp, "vy": math.sin(a) * sp - 0.2 * self.h,
                "life": 0.0, "max": random.uniform(0.5, 0.9),
                "col": PADS[pad]["color"] if not stars else (255, 225, 80),
                "size": self.h * (0.022 if stars else 0.014), "star": stars,
                "rot": random.uniform(0, 6.28), "spin": random.uniform(-6, 6),
            })
        del self.particles[:-MAX_PARTICLES]

    def hit_visual(self, pad, t):
        self.glow[pad] = 1.0
        if pad == "kick":
            self.kick_flash = 1.0
        else:
            self.ring(pad, t)
            self.burst(pad)

    def demo(self, pad, t):
        # Le jeu "montre" un pad (son + lumiere) sans que l'enfant ait frappe
        self.play(PADS[pad]["sound"])
        self.hit_visual(pad, t)

    def reward(self, pad, t):
        self.play("sparkle")
        self.burst(pad, n=10, stars=True)

    def celebrate(self, t):
        self.play("jingle")
        for _ in range(45):
            a = random.uniform(0, 2 * math.pi)
            sp = random.uniform(0.3, 1.0) * self.h
            self.particles.append({
                "x": self.w / 2, "y": self.h * 0.45,
                "vx": math.cos(a) * sp, "vy": math.sin(a) * sp - 0.3 * self.h,
                "life": 0.0, "max": random.uniform(1.0, 1.6),
                "col": random.choice([PADS[p]["color"] for p in GAME_PADS] + [(255, 255, 255)]),
                "size": self.h * random.uniform(0.025, 0.045), "star": True,
                "rot": random.uniform(0, 6.28), "spin": random.uniform(-5, 5),
            })
        del self.particles[:-MAX_PARTICLES]

    # --- entree -----------------------------------------------------------
    #
    # Une frappe = strike() (debounce + son, tout de suite, n'importe quel
    # thread) puis react() (visuel + logique du mode, thread principal).

    def strike(self, kind, name, t):
        """Retourne True si l'evenement doit etre transmis a react()."""
        if name not in PADS:
            return name in ("start", "select")
        if kind != "down":
            return False
        with self.strike_lock:
            if t - self.last_down[name] < DEBOUNCE:
                return False
            self.last_down[name] = t
        self.play(PADS[name]["sound"])
        return True

    def react(self, kind, name, t):
        if name == "start":
            self.start_down_at = t if kind == "down" else None
            return
        if name == "select":
            return
        self.last_hit = t
        self.hit_visual(name, t)
        self.mode.on_hit(self, name, t)

    def handle(self, kind, name, t):
        # Point d'entree unique (clavier, tests) : son puis reaction
        if self.strike(kind, name, t):
            self.react(kind, name, t)

    def kit_event(self, kind, name, t):
        # Appele depuis le thread kit
        if self.strike(kind, name, t):
            self.kit_queue.append((kind, name, t))

    def process_kit(self):
        while self.kit_queue:
            self.react(*self.kit_queue.popleft())

    def process_pygame(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                if ev.key == pygame.K_ESCAPE:
                    self.running = False
                elif ev.key in KEYBOARD:
                    kind = "down" if ev.type == pygame.KEYDOWN else "up"
                    self.handle(kind, KEYBOARD[ev.key], time.monotonic())

    def switch_mode(self, t):
        self.mode_idx = (self.mode_idx + 1) % len(self.modes)
        self.mode = self.modes[self.mode_idx]
        self.effects.clear()
        self.mode.enter(self, t)
        self.banner_until = t + 1.5
        self.play("jingle", 0.5)

    # --- boucle -----------------------------------------------------------

    def update(self, t, dt):
        if self.start_down_at is not None and t - self.start_down_at >= HOLD_TO_SWITCH:
            self.start_down_at = None
            self.switch_mode(t)
        k = math.exp(-dt / 0.22)
        for p in self.glow:
            self.glow[p] *= k
        self.kick_flash *= math.exp(-dt / 0.15)
        self.effects = [e for e in self.effects if t - e["t0"] < e["dur"]]
        g = 0.9 * self.h
        alive = []
        for p in self.particles:
            p["life"] += dt
            if p["life"] < p["max"]:
                p["x"] += p["vx"] * dt
                p["y"] += p["vy"] * dt
                p["vy"] += g * dt
                p["vx"] *= 0.985
                p["rot"] += p["spin"] * dt
                alive.append(p)
        self.particles = alive
        self.mode.update(self, t)

    def draw_pad(self, surf, pad):
        cx, cy, rx, ry = self.geo[pad]
        base = PADS[pad]["color"]
        g = self.glow[pad]
        s = 1.0 + 0.10 * g
        fill = lerp(DARK_FILL, base, 0.12 + 0.88 * g)
        rim = lerp(base, WHITE, 0.5 * g)
        shape = PADS[pad]["shape"]
        if shape == "drum":
            r = int(rx * s)
            pygame.draw.circle(surf, fill, (cx, cy), r)
            pygame.draw.circle(surf, rim, (cx, cy), r, max(4, int(r * 0.11)))
        else:
            rect = pygame.Rect(0, 0, int(2 * rx * s), int(2 * ry * s))
            rect.center = (cx, cy)
            if shape == "cymbal":
                pygame.draw.ellipse(surf, fill, rect)
                pygame.draw.ellipse(surf, rim, rect, max(4, int(ry * 0.14)))
            else:
                rad = rect.height // 2
                pygame.draw.rect(surf, fill, rect, border_radius=rad)
                pygame.draw.rect(surf, rim, rect, max(3, int(rect.height * 0.15)), border_radius=rad)

    def draw_target(self, surf, pad, t):
        # Anneau blanc pulsant autour du pad attendu
        cx, cy, rx, ry = self.geo[pad]
        pulse = 0.5 + 0.5 * math.sin(t * 6.0)
        grow = 1.12 + 0.10 * pulse
        col = lerp(PADS[pad]["color"], WHITE, 0.4 + 0.6 * pulse)
        width = max(4, int(self.h * 0.012))
        rect = pygame.Rect(0, 0, int(2 * rx * grow), int(2 * ry * grow))
        rect.center = (cx, cy)
        pygame.draw.ellipse(surf, col, rect, width)

    def draw_approach(self, surf, pad, frac):
        # frac = 1 un temps avant, 0 pile sur le temps
        cx, cy, rx, ry = self.geo[pad]
        s = 1.0 + 1.6 * frac
        col = lerp(WHITE, PADS[pad]["color"], frac * 0.6)
        rect = pygame.Rect(0, 0, int(2 * rx * s), int(2 * ry * s))
        rect.center = (cx, cy)
        pygame.draw.ellipse(surf, col, rect, max(4, int(self.h * 0.01)))

    def draw_mode_dots(self, surf, t):
        # Coin superieur droit : 3 points, le mode actif est plein
        r = int(self.h * 0.012)
        for i in range(len(self.modes)):
            x = int(self.w - self.h * 0.04 - (len(self.modes) - 1 - i) * r * 3)
            y = int(self.h * 0.04)
            if i == self.mode_idx:
                pygame.draw.circle(surf, (200, 200, 220), (x, y), r)
            else:
                pygame.draw.circle(surf, (80, 80, 100), (x, y), r, 2)
        # Jauge d'appui long sur START
        if self.start_down_at is not None:
            p = (t - self.start_down_at) / HOLD_TO_SWITCH
            if p > 0.1:
                rr = int(self.h * 0.035)
                cx, cy = int(self.w - self.h * 0.06), int(self.h * 0.11)
                rect = pygame.Rect(cx - rr, cy - rr, 2 * rr, 2 * rr)
                pygame.draw.arc(surf, WHITE, rect, math.pi / 2, math.pi / 2 + 2 * math.pi * min(1.0, p), 5)
        # Banniere de changement de mode : N gros points entre les cymbales
        if t < self.banner_until:
            n = self.mode.dots
            r = int(self.h * 0.05)
            x0 = self.w / 2 - (n - 1) * r * 1.5
            for i in range(n):
                pygame.draw.circle(surf, WHITE, (int(x0 + i * r * 3), int(self.h * 0.345)), r)

    def draw(self, t):
        surf = self.screen
        surf.fill(lerp(BG, PADS["kick"]["color"], 0.18 * self.kick_flash))
        for e in self.effects:
            p = (t - e["t0"]) / e["dur"]
            ease = 1 - (1 - p) ** 2
            r = int(e["r0"] + (e["r1"] - e["r0"]) * ease)
            pygame.draw.circle(surf, lerp(e["col"], BG, p), e["c"], r, e["w"])
        for pad in PADS:
            self.draw_pad(surf, pad)
        self.mode.draw(self, surf, t)
        for p in self.particles:
            fade = p["life"] / p["max"]
            col = lerp(p["col"], BG, fade ** 2)
            size = p["size"] * (1 - 0.6 * fade)
            if p["star"]:
                pygame.draw.polygon(surf, col, star_points(p["x"], p["y"], size, p["rot"]))
            else:
                pygame.draw.circle(surf, col, (int(p["x"]), int(p["y"])), max(1, int(size)))
        self.draw_mode_dots(surf, t)

    def step(self):
        t = time.monotonic()
        dt = min(0.1, t - self.last_t)
        self.last_t = t
        self.update(t, dt)
        self.draw(t)
        pygame.display.flip()

    def run(self):
        # Le son des frappes du kit part depuis le thread kit ; cette boucle ne
        # fait que le rendu, cadence a FPS (ou par le vsync de display.flip).
        frame = 1.0 / FPS
        next_frame = time.monotonic()
        try:
            while self.running:
                remaining = next_frame - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                next_frame += frame
                if time.monotonic() - next_frame > frame:
                    next_frame = time.monotonic() + frame
                self.process_kit()
                self.process_pygame()
                self.step()
        except KeyboardInterrupt:
            pass
        finally:
            if self.inp is not None:
                self.inp.stop()
            pygame.quit()


def main():
    ap = argparse.ArgumentParser(description="PiDrum")
    ap.add_argument("--window", action="store_true", help="fenetre 1280x720 au lieu du plein ecran")
    ap.add_argument("--keyboard-only", action="store_true", help="ignorer le kit, clavier seulement")
    args = ap.parse_args()
    App(window=args.window, use_kit=not args.keyboard_only).run()


if __name__ == "__main__":
    sys.exit(main())
