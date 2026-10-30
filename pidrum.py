#!/usr/bin/env python3
# PiDrum - batterie d'apprentissage pour jeune enfant
# Kit de batterie Guitar Hero (PS3) + Raspberry Pi 4 + TV
#
# Aucun texte a l'ecran : tout passe par les couleurs, les sons et les animations.
# Habillage facon Guitar Hero / Rock Band : autoroute de notes en perspective,
# gemmes de couleur, ligne de frappe, flammes, jauge d'energie, foule et projecteurs.
#
# Modes (appui LONG sur START du kit, ~1.5 s, pour passer au suivant) :
#   1. Jeu libre  : chaque pad joue son son ; accompagnement musical selon le niveau
#   2. Copie-moi  : des gemmes montrent une sequence, l'enfant la reproduit
#   3. Le beat    : frapper les gemmes qui arrivent, au rythme du metronome
#   4. Chanson    : une comptine joue, les notes descendent l'autoroute
#
# Boutons de facade (appui LONG, ~0.8 s) : 6 niveaux par jeu, de apprenti a expert
#   triangle = niveau plus dur    X (croix) = niveau plus facile
#   rond     = mode suivant       carre     = mode precedent
#
# SELECT + START maintenus 3 s : extinction propre du Pi
#
# Clavier (tests / parent) : 1-5 = pads, espace = pedale, Entree = START,
#                            Tab = SELECT, fleches = boutons de facade
#                            (haut triangle, bas X, droite rond, gauche carre),
#                            Echap = quitter

import argparse
import collections
import json
import math
import os
import random
import select
import subprocess
import sys
import threading
import time

# Doit etre defini avant l'import de pygame pour masquer le message d'accueil
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

import songs  # noqa: E402
import sounds  # noqa: E402
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
JUDGE_OFFSET = 0.0          # (s) compense la latence TV si les frappes semblent en retard
HOLD_TO_SWITCH = 1.5        # duree d'appui sur START pour changer de mode
SHUTDOWN_HOLD = 3.0         # (s) SELECT + START maintenus ensemble pour eteindre le Pi
SHUTDOWN_FADE = 2.0         # (s) son d'au revoir et fondu avant la commande
SHUTDOWN_CMD = ["sudo", "-n", "/usr/bin/systemctl", "poweroff"]   # voir README (sudoers)
VEL_MIN = 35                # force d'un coup doux (rapport HID, 0-127 ; mesure 28-40)
VEL_MAX = 110               # force d'un coup fort (mesure 80-122)
VEL_SOFT_GAIN = 0.9         # niveau d'un coup tres doux (1.0 = son normal)
VEL_HARD_GAIN = 1.5         # niveau d'un coup tres fort (> 1 : variante "_loud" saturee)
VEL_GAMMA = 1.0             # courbe : 1 = lineaire, < 1 = coups moyens deja forts
VEL_FRESH = 0.1             # (s) une force plus vieille que ca n'appartient pas au coup
DEBOUNCE = 0.08             # ignore les doubles declenchements d'un meme pad
                            # (le pad vert du kit rebondit ~60 ms apres le coup)
KICK_IN_GAMES = False       # inclure la pedale dans les jeux (Copie-moi, Le beat, Chanson)
IDLE_HINT_AFTER = 20.0      # (s) sans frappe avant d'inviter l'enfant a jouer
IDLE_HINT_EVERY = 3.0       # (s) entre deux invitations
MAX_PARTICLES = 500
RECONNECT_EVERY = 2.0       # (s) entre deux recherches du kit

# --- Boutons de facade (carre, triangle, rond, X) ---------------------------
# Sur le GH World Tour PS3, chaque bouton de facade envoie le MEME code que le pad
# de sa couleur (rond = rouge, carre = bleu, X = vert, triangle = jaune). Un coup
# de baguette dure quelques ms : seul un appui maintenu BUTTON_HOLD compte comme
# bouton. Si le kit a des codes propres, map_pads.py les ecrit dans pads.json
# ("buttons") et ils remplacent ce partage.
BUTTON_HOLD = 0.8           # (s) appui maintenu sur un bouton de facade
BUTTON_PADS = {"triangle": "yellow", "circle": "red", "cross": "green", "square": "blue"}
BUTTON_ACTIONS = {"triangle": "level_up", "cross": "level_down",
                  "circle": "next_mode", "square": "prev_mode"}

# --- Niveaux (choisis par le parent, memorises dans progress.json) ----------
LEVELS = ["apprenti", "novice", "debutant", "intermediaire", "avance", "expert"]
DEFAULT_LEVEL = 0
LEVEL_BANNER = 1.6          # (s) affichage de l'escalier des niveaux apres un changement
# Couleurs des marches, du plus facile au plus dur (facon difficultes Guitar Hero)
LEVEL_COLORS = [(40, 195, 85), (140, 215, 50), (250, 205, 30),
                (255, 125, 20), (225, 40, 55), (170, 95, 230)]
DRUM_PADS = ["red", "blue", "green"]        # "drums" dans les tables : peaux seulement

# Jeu libre : accompagnement en boucle (songs.GROOVES), None = silence
FREE_LEVELS = [None, "pulse", "walk", "pop", "rock", "funk"]
FREE_GROOVE_VOLUME = 0.45
FREE_LEAD = 2.0             # (s) trajet d'une ligne de mesure sur l'autoroute
IDLE_SCROLL_BPM = 60        # defilement de l'autoroute sans musique

# Copie-moi : pads proposes, longueur de sequence min/max, ecart entre deux
# gemmes montrees (s), delai avant d'indiquer le pad attendu (s)
COPY_LEVELS = [
    {"pads": "drums", "min": 1, "max": 1, "step": 1.0, "hint": 0.0},
    {"pads": "all", "min": 1, "max": 2, "step": 0.9, "hint": 2.5},
    {"pads": "all", "min": 2, "max": 3, "step": 0.85, "hint": 2.5},
    {"pads": "all", "min": 2, "max": 4, "step": 0.75, "hint": 3.0},
    {"pads": "all", "min": 3, "max": 5, "step": 0.65, "hint": 3.5},
    {"pads": "all", "min": 4, "max": 6, "step": 0.55, "hint": 4.0},
]
COPY_LEAD = 1.2             # (s) trajet d'une gemme montree jusqu'a la ligne de frappe
COPY_START_DELAY = 1.0      # (s) avant la premiere demonstration
COPY_REPLAY_AFTER = 12.0    # (s) d'inactivite avant de rejouer la sequence
COPY_WINS_TO_GROW = 3       # reussites avant d'allonger la sequence
COPY_ERRORS_TO_SHRINK = 5   # erreurs dans une manche avant de la raccourcir
COPY_PAUSE_WIN = 2.2        # (s) de fete apres une sequence reussie

# Le beat : tempo, tolerance (s), pas par temps (2 = croches), bonnes frappes
# avant la fanfare, motifs (une lettre par pas, "." = silence)
BEAT_LEVELS = [
    {"bpm": 60, "window": 0.30, "sub": 1, "goal": 6, "patterns": ["r", "b", "g"]},
    {"bpm": 70, "window": 0.25, "sub": 1, "goal": 8,
     "patterns": ["y", "o", "rb", "rg", "yr", "k", "kr"]},
    {"bpm": 76, "window": 0.22, "sub": 1, "goal": 8,
     "patterns": ["rbgb", "ryro", "rrb.", "bgbr", "krkr"]},
    {"bpm": 84, "window": 0.20, "sub": 2, "goal": 12,
     "patterns": ["y.r.y.r.", "yyr.yyr.", "r.rbg.g.", "k.r.k.r."]},
    {"bpm": 96, "window": 0.17, "sub": 2, "goal": 12,
     "patterns": ["yyryyyry", "rrbbggbb", "oyryoyry", "kyrykyry"]},
    {"bpm": 108, "window": 0.15, "sub": 2, "goal": 16,
     "patterns": ["yryryryr", "rbgorbgo", "yyrbyyrg", "kykrkykr"]},
]
PATTERN_PADS = {"r": "red", "y": "yellow", "b": "blue", "o": "orange", "g": "green", "k": "kick"}
BEAT_LEAD = 1.8             # (s) trajet d'une gemme sur l'autoroute
BEAT_LEAD_IN = 2            # temps de metronome avant le premier temps juge
BEAT_REST_AFTER_GOAL = 4    # temps de pause apres la fanfare
BEAT_MISSES_TO_DROP = 12    # gemmes ratees d'affilee (enfant actif) avant de redescendre
BEAT_ACTIVE = 3.0           # (s) une gemme ratee ne compte que si l'enfant joue

# Chanson : densite de la partition (songs.chart), pads, tolerance (s),
# trajet d'une note (s, doit rester <= decompte, voir test)
SONG_LEVELS = [
    {"density": 0, "pads": "drums", "window": 0.35, "lead": 2.6},
    {"density": 1, "pads": "drums", "window": 0.32, "lead": 2.6},
    {"density": 1, "pads": "all", "window": 0.30, "lead": 2.5},
    {"density": 2, "pads": "all", "window": 0.26, "lead": 2.3},
    {"density": 3, "pads": "all", "window": 0.22, "lead": 2.1},
    {"density": 4, "pads": "all", "window": 0.18, "lead": 1.9},
]
SONG_VOLUME = 0.7           # volume de la musique (les frappes restent au-dessus)
SONG_START_DELAY = 1.0      # (s) avant le decompte de la premiere chanson
SONG_PAUSE = 3.0            # (s) de fete entre deux chansons
SONG_LEVEL_UP = 0.7         # taux de reussite pour remonter vers le niveau choisi
SONG_LEVEL_DOWN = 0.3       # taux en dessous duquel on allege
SONG_MISS_FADE = 0.4        # (s) disparition douce d'une note manquee
SONG_CUE = 0.3              # fraction du trajet ou la piste visee s'allume
SONG_PAD_ORDER = ["red", "blue", "green", "orange", "yellow", "kick"]  # grave -> aigu

# --- Energie et "star power" (uniquement positif : ne descend jamais) -------
ENERGY_HIT = 0.08           # par bonne frappe dans un jeu
ENERGY_FREE = 0.025         # par frappe en jeu libre
STAR_TIME = 8.0             # (s) de star power quand la jauge est pleine
ENERGY_CELLS = 10

# --- Scene facon Guitar Hero (fractions de l'ecran) -------------------------
HIGHWAY_WIDTH = 0.62        # largeur de l'autoroute a la ligne de frappe (fraction de w)
HIGHWAY_FAR = 0.28          # largeur au loin / largeur a la ligne de frappe
HIGHWAY_TOP = 0.11          # haut de l'autoroute (fraction de h)
STRIKE_Y = 0.80             # ligne de frappe (fraction de h)
FRET_SIZE = 0.42            # rayon d'une cible / largeur de piste
GEM_SIZE = 0.36             # rayon d'une gemme / largeur de piste
GEM_TILT = 0.5              # ellipse : hauteur / largeur (autoroute vue de biais)
KICK_BAR = 0.022            # demi-hauteur de la barre de pedale (fraction de h)
CROWD_SIZE = 7              # personnes de chaque cote

BG = (16, 16, 32)
DARK_FILL = (34, 34, 52)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
SHUTDOWN_GAUGE = (90, 140, 255)
STAGE_TOP = (8, 6, 20)
STAGE_BOTTOM = (34, 18, 48)
BEAM = (120, 110, 200)
HIGHWAY_COL = (18, 18, 28)
LANE_LINE = (46, 46, 64)
RAIL = (120, 120, 150)
RAIL_HOT = (255, 200, 60)
STAR_BLUE = (90, 180, 255)
CROWD_COL = (52, 38, 78)
SPEAKER = (26, 24, 34)

# Disposition : les pistes de l'autoroute dans l'ordre Guitar Hero World Tour,
# qui est aussi l'ordre gauche -> droite du kit vu par l'enfant
PADS = {
    "red":    {"color": (225, 40, 55),  "shape": "drum",   "sound": "snare"},
    "yellow": {"color": (250, 205, 30), "shape": "cymbal", "sound": "hihat"},
    "blue":   {"color": (45, 115, 240), "shape": "drum",   "sound": "tom_hi"},
    "orange": {"color": (255, 125, 20), "shape": "cymbal", "sound": "crash"},
    "green":  {"color": (40, 195, 85),  "shape": "drum",   "sound": "tom_lo"},
    "kick":   {"color": (170, 95, 230), "shape": "bar",    "sound": "kick"},
}
GAME_PADS = ["red", "yellow", "blue", "orange", "green"]
LANES = GAME_PADS
CONTROLS = ("start", "select") + tuple(BUTTON_PADS)

KEYBOARD = {
    pygame.K_1: "red", pygame.K_2: "yellow", pygame.K_3: "blue",
    pygame.K_4: "orange", pygame.K_5: "green", pygame.K_SPACE: "kick",
    pygame.K_RETURN: "start", pygame.K_TAB: "select",
    pygame.K_UP: "triangle", pygame.K_DOWN: "cross",
    pygame.K_RIGHT: "circle", pygame.K_LEFT: "square",
}

HERE = os.path.dirname(os.path.abspath(__file__))
PADS_FILE = os.path.join(HERE, "pads.json")
PROGRESS_FILE = os.path.join(HERE, "progress.json")


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


def kit_keys(cfg):
    """Roles lus sur le kit : pads.json "keys" + boutons de facade a code propre.

    Un bouton dont le code est deja celui d'un pad est ignore ici : il reste
    partage avec son pad et detecte par l'appui long (BUTTON_PADS).
    """
    keys = dict(cfg.get("keys", {}))
    used = set()
    for code in keys.values():
        used.update(code if isinstance(code, list) else [code])
    for button, code in cfg.get("buttons", {}).items():
        codes = code if isinstance(code, list) else [code]
        if button in BUTTON_PADS and not used.intersection(codes):
            keys[button] = code
    return keys


def shared_buttons(cfg):
    """pad -> bouton de facade qui partage son code (appui long)."""
    own = set(kit_keys(cfg or {})) & set(BUTTON_PADS)
    return {pad: b for b, pad in BUTTON_PADS.items() if b not in own}


def lane_pool(app, which):
    """Pads d'une table de niveau : "drums" (peaux) ou "all" ; jamais vide."""
    pool = app.target_pads()
    if which == "drums":
        drums = [p for p in pool if p in DRUM_PADS]
        if drums:
            return drums
    return pool


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
        self.decoder = KeyDecoder(kit_keys(cfg))
        vel_cfg = cfg.get("velocity", {}).get("report_bytes", {})
        self.velocity = VelocityDecoder(vel_cfg) if vel_cfg else None
        self.callback = callback
        self.dev = None
        self.hid = None
        self.hid_warned = False
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

    def _open_hid(self):
        """Rapports HID bruts du kit (force de frappe), ou None (volume plein)."""
        if self.velocity is None:
            return None
        hid = HidRaw.find(self.name)
        if hid is None and not self.hid_warned:
            print("Force de frappe indisponible (hidraw illisible ? voir README, regle udev)")
            self.hid_warned = True
        elif hid is not None:
            print("Force de frappe : %s" % hid.path)
        return hid

    def _close(self):
        for d in (self.dev, self.hid):
            try:
                d.close()
            except Exception:
                pass
        self.dev = self.hid = None
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
                self.hid = self._open_hid()
            try:
                fds = [self.dev.fd] + ([self.hid.fd] if self.hid is not None else [])
                ready, _, _ = select.select(fds, [], [], 0.5)
                if not ready:
                    continue
                # Le noyau remet le rapport brut (hidraw) AVANT les evenements evdev
                # qui en decoulent : lire hidraw d'abord donne la force du coup
                if self.hid is not None:
                    try:
                        for report in self.hid.read_reports():
                            self.velocity.feed(report)
                    except OSError:
                        self.hid.close()
                        self.hid = None
                if self.dev.fd not in ready:
                    continue
                for ev in self.dev.read():
                    for kind, role in self.decoder.feed(ev.type, ev.code, ev.value):
                        vel = self.velocity.take(role) if (self.velocity and kind == "down") else None
                        self.callback(kind, role, time.monotonic(), vel)
            except BlockingIOError:
                continue
            except (OSError, ValueError):
                print("Kit deconnecte")
                self._close()
                time.sleep(RECONNECT_EVERY)


class HidRaw:
    """Lecture non bloquante de /dev/hidrawN (rapports bruts du kit)."""
    REPORT_MAX = 64

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    @staticmethod
    def find(device_name, sysfs="/sys/class/hidraw"):
        try:
            nodes = sorted(os.listdir(sysfs))
        except OSError:
            return None
        for node in nodes:
            try:
                with open(os.path.join(sysfs, node, "device", "uevent")) as f:
                    if "HID_NAME=%s\n" % device_name not in f.read():
                        continue
                return HidRaw(os.path.join("/dev", node))
            except OSError:
                continue
        return None

    def read_reports(self):
        out = []
        while True:
            try:
                data = os.read(self.fd, self.REPORT_MAX)
            except BlockingIOError:
                return out
            if not data:
                return out
            out.append(data)

    def close(self):
        os.close(self.fd)


class VelocityDecoder:
    """Force de chaque pad depuis les rapports HID bruts.

    report_bytes : role -> index de l'octet de pression dans le rapport
    (GH World Tour PS3 : rouge 12, vert 13, bleu 14, orange 16 ; 0 = pas frappe).
    """

    def __init__(self, report_bytes):
        self.index = {role: int(i) for role, i in report_bytes.items()}
        self.prev = {}
        self.last = {}     # role -> (force, instant) au front montant

    def feed(self, report, now=None):
        now = time.monotonic() if now is None else now
        for role, i in self.index.items():
            v = report[i] if i < len(report) else 0
            # Seul le front montant compte : une pression residuelle ou parasite
            # (diaphonie d'un pad voisin) ne doit pas gonfler le coup suivant
            if v > 0 and self.prev.get(role, 0) == 0:
                self.last[role] = (v, now)
            self.prev[role] = v

    def take(self, role, now=None):
        """Force du coup qui vient d'arriver pour ce role, ou None (puis oubliee)."""
        now = time.monotonic() if now is None else now
        v = self.last.pop(role, None)
        if v is None or now - v[1] > VEL_FRESH:
            return None
        return v[0]


def velocity_gain(vel):
    """Force brute (0-127) -> niveau relatif au son normal. None = inconnue = 1.0."""
    if vel is None:
        return 1.0
    p = (min(max(vel, VEL_MIN), VEL_MAX) - VEL_MIN) / float(VEL_MAX - VEL_MIN)
    return VEL_SOFT_GAIN + (VEL_HARD_GAIN - VEL_SOFT_GAIN) * p ** VEL_GAMMA


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

class Mode:
    key = ""
    dots = 0
    LEVELS = [None] * len(LEVELS)   # reglages par niveau, un par entree de LEVELS

    def __init__(self):
        self.home = DEFAULT_LEVEL     # niveau choisi par le parent (triangle / X)
        self.level = DEFAULT_LEVEL    # niveau joue : redescend seul, remonte jusqu'a home

    def cfg(self):
        return self.LEVELS[self.level]

    def auto_down(self):
        # Difficulte qui redescend toute seule (regle 4)
        if self.level > 0:
            self.level -= 1
            return True
        return False

    def auto_up(self):
        # Remonte apres des reussites, sans depasser le niveau choisi par le parent
        if self.level < self.home:
            self.level += 1
            return True
        return False

    def enter(self, app, t):
        pass

    def on_hit(self, app, pad, t):
        pass

    def update(self, app, t):
        pass

    def draw(self, app, surf, t):
        # Dessine au-dessus des cibles (gemmes)
        pass

    def draw_back(self, app, surf, t):
        # Dessine sur l'autoroute, sous les cibles (pistes allumees, lignes de mesure)
        pass

    def draw_hud(self, app, surf, t):
        # Indicateurs propres au mode (progression)
        pass

    def leave(self, app):
        # Appele quand on quitte le mode (changement de mode ou de niveau, extinction)
        pass


class FreePlay(Mode):
    key = "free"
    dots = 1
    LEVELS = FREE_LEVELS

    def enter(self, app, t):
        self.next_invite = 0.0
        self.channel = None
        self.t0 = t
        groove = self.cfg()
        if groove and groove in songs.GROOVES:
            self.period = 60.0 / songs.GROOVES[groove]["bpm"]
            self.channel = app.play("groove_" + groove, FREE_GROOVE_VOLUME, loops=-1)
        else:
            self.period = 60.0 / IDLE_SCROLL_BPM

    def leave(self, app):
        if self.channel is not None:
            self.channel.stop()
            self.channel = None

    def on_hit(self, app, pad, t):
        app.gain_energy(ENERGY_FREE, t)

    def update(self, app, t):
        # Apres un moment sans frappe, un pad "appelle" doucement l'enfant
        if t - app.last_hit > IDLE_HINT_AFTER and t >= self.next_invite:
            app.ring(random.choice(app.target_pads()), t, strong=False)
            self.next_invite = t + IDLE_HINT_EVERY

    def draw_back(self, app, surf, t):
        app.draw_beat_lines(surf, t, self.t0, self.period, FREE_LEAD)


class CopyMe(Mode):
    key = "copy"
    dots = 2
    LEVELS = COPY_LEVELS

    def enter(self, app, t):
        self.app = app
        self.t_scroll = t
        self.setup(t + COPY_START_DELAY)

    def setup(self, start_t):
        c = self.cfg()
        self.step = c["step"]
        self.hint = c["hint"]
        self.length = c["min"]
        self.wins = 0
        self.new_round(start_t)

    def new_round(self, start_t):
        pool = lane_pool(self.app, self.cfg()["pads"])
        seq = []
        for _ in range(self.length):
            choices = [p for p in pool if not seq or p != seq[-1]] or pool
            seq.append(random.choice(choices))
        self.seq = seq
        self.start_show(start_t)

    def start_show(self, start_t):
        # Les gemmes partent du fond : la premiere touche la ligne COPY_LEAD plus tard
        self.idx = 0
        self.errors = 0
        self.state = "show"
        self.show_t0 = start_t + COPY_LEAD
        self.shown = 0

    def update(self, app, t):
        if self.state == "show":
            while self.shown < len(self.seq) and t >= self.show_t0 + self.shown * self.step:
                app.demo(self.seq[self.shown], t)
                self.shown += 1
            if t >= self.show_t0 + (len(self.seq) - 1) * self.step + 0.3:
                self.state = "wait"
                self.wait_t0 = t
        elif self.state == "wait":
            # L'enfant ne fait rien : on rejoue la sequence
            if t - self.wait_t0 > COPY_REPLAY_AFTER:
                self.start_show(t)
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
                if self.wins >= COPY_WINS_TO_GROW:
                    self.wins = 0
                    if self.length < self.cfg()["max"]:
                        self.length += 1
                    elif self.auto_up():
                        self.setup(t)
                self.state = "pause"
                self.pause_until = t + COPY_PAUSE_WIN
        else:
            # Aucun son negatif : on montre simplement le bon pad
            self.errors += 1
            self.wait_t0 = t - self.hint
            if self.errors >= COPY_ERRORS_TO_SHRINK:
                if self.length > self.cfg()["min"]:
                    self.length -= 1
                    self.wins = 0
                elif self.auto_down():
                    self.setup(t)
                else:
                    return
                self.state = "pause"
                self.pause_until = t + 1.0

    def hinted(self, t):
        if self.state != "wait":
            return None
        if self.length == 1 or t - self.wait_t0 >= self.hint:
            return self.seq[self.idx]
        return None

    def draw_back(self, app, surf, t):
        app.draw_beat_lines(surf, t, self.t_scroll, 60.0 / IDLE_SCROLL_BPM, FREE_LEAD)
        pad = self.hinted(t)
        if pad:
            app.light_lane(surf, pad, 0.25 + 0.15 * math.sin(t * 6.0))

    def draw(self, app, surf, t):
        if self.state == "show":
            # Les gemmes de la sequence descendent et "jouent" en touchant la ligne
            for i in range(len(self.seq) - 1, self.shown - 1, -1):
                z = (self.show_t0 + i * self.step - t) / COPY_LEAD
                app.draw_gem(surf, self.seq[i], z)
        pad = self.hinted(t)
        if pad:
            app.draw_target(surf, pad, t)

    def draw_hud(self, app, surf, t):
        # Longueur de la sequence : une petite gemme par pad a reproduire
        if self.state in ("wait", "pause"):
            app.draw_pips(surf, len(self.seq), self.idx if self.state == "wait" else len(self.seq))


class Beat(Mode):
    key = "beat"
    dots = 3
    LEVELS = BEAT_LEVELS

    def enter(self, app, t):
        self.app = app
        self.setup(t)

    def setup(self, t):
        c = self.cfg()
        self.period = 60.0 / c["bpm"]
        self.sub = c["sub"]
        self.step = self.period / self.sub
        # Fenetre jamais plus large qu'un demi-pas : chaque frappe a un seul pas possible
        self.window = min(c["window"], self.step * 0.5)
        self.goal = c["goal"]
        allowed = set(self.app.target_pads())
        pats = []
        for p in c["patterns"]:
            steps = [PATTERN_PADS.get(ch) for ch in p]
            # Un motif avec un element desactive ou exclu ne pourrait jamais etre reussi
            if {s for s in steps if s} <= allowed:
                pats.append(steps)
        self.patterns = pats or [[self.app.target_pads()[0]]]
        self.pattern_idx = 0
        self.restart(t + BEAT_LEAD_IN * self.period)

    def restart(self, t0):
        self.t0 = t0
        self.good = 0
        self.hit_steps = set()
        self.last_tick = -1
        self.last_beat_t = -10.0
        self.judged = -1          # dernier pas deja juge (rate ou reussi)
        self.misses = 0

    def target(self, n):
        if n < 0:
            return None
        p = self.patterns[self.pattern_idx]
        return p[n % len(p)]

    def update(self, app, t):
        n = math.floor((t - self.t0) / self.period)
        if n > self.last_tick and n >= 0:
            app.play("tick_accent" if n % 4 == 0 else "tick")
            self.last_beat_t = self.t0 + n * self.period
            self.last_tick = n
        # Gemmes passees sans etre frappees : compteur discret, jamais montre
        tj = t - JUDGE_OFFSET
        while tj > self.t0 + (self.judged + 1) * self.step + self.window:
            k = self.judged + 1
            self.judged = k
            due = self.t0 + k * self.step
            if self.target(k) and k not in self.hit_steps and app.last_hit > due - BEAT_ACTIVE:
                self.misses += 1
        if self.misses >= BEAT_MISSES_TO_DROP:
            if self.auto_down():
                self.setup(t)
            else:
                self.misses = 0

    def on_hit(self, app, pad, t):
        tj = t - JUDGE_OFFSET
        n = int(round((tj - self.t0) / self.step))
        if n < 0 or n in self.hit_steps:
            return
        if abs(tj - (self.t0 + n * self.step)) <= self.window and self.target(n) == pad:
            self.hit_steps.add(n)
            self.good += 1
            self.misses = 0
            app.reward(pad, t)
            if self.good >= self.goal:
                app.celebrate(t)
                self.pattern_idx += 1
                if self.pattern_idx >= len(self.patterns) and self.auto_up():
                    self.setup(t + BEAT_REST_AFTER_GOAL * self.period)
                    return
                self.pattern_idx %= len(self.patterns)
                beat = math.ceil((n + 1) / self.sub)
                self.restart(self.t0 + (beat + BEAT_REST_AFTER_GOAL) * self.period)

    def draw_back(self, app, surf, t):
        app.draw_beat_lines(surf, t, self.t0, self.period, BEAT_LEAD)
        # La piste de la prochaine gemme s'allume doucement
        k = max(0, math.ceil((t - self.window - self.t0) / self.step))
        for j in range(k, k + 2 * self.sub + 1):
            pad = self.target(j)
            if pad and j not in self.hit_steps:
                if self.t0 + j * self.step - t < self.period:
                    app.light_lane(surf, pad, 0.22)
                break

    def draw(self, app, surf, t):
        first = math.floor((t - self.t0) / self.step) - 1
        last = math.ceil((t + BEAT_LEAD - self.t0) / self.step)
        for k in range(last, first - 1, -1):
            pad = self.target(k)
            if pad and k not in self.hit_steps:
                app.draw_gem(surf, pad, (self.t0 + k * self.step - t) / BEAT_LEAD)

    def draw_hud(self, app, surf, t):
        app.draw_pips(surf, self.goal, self.good)


class Song(Mode):
    """Comptine + notes qui descendent l'autoroute, facon Rock Band.

    Pas d'echec : la chanson va toujours au bout, une note manquee s'efface.
    """
    key = "song"
    dots = 4
    LEVELS = SONG_LEVELS

    def enter(self, app, t):
        self.app = app
        self.song_idx = 0
        self.channel = None
        self.t_scroll = t
        self.start_song(t + SONG_START_DELAY)

    def leave(self, app):
        if self.channel is not None:
            self.channel.stop()
            self.channel = None

    def start_song(self, t0):
        self.song = songs.SONGS[self.song_idx]
        self.period = 60.0 / self.song["bpm"]
        c = self.cfg()
        self.window = c["window"]
        self.lead = c["lead"]
        self.start_at = t0
        self.audio_t0 = None
        self.state = "wait"
        pool = lane_pool(self.app, c["pads"])
        order = [p for p in SONG_PAD_ORDER if p in pool]
        self.chart = songs.chart(self.song, c["density"], order)
        self.notes = []
        self.hits = 0

    def begin_play(self, t):
        # Les notes sont calees sur l'instant reel ou la musique part
        self.audio_t0 = t
        self.channel = self.app.play("song_" + self.song["id"], SONG_VOLUME)
        base = t + songs.COUNT_IN * self.period
        self.notes = [{"t": base + b * self.period, "pad": pad, "state": "pending", "done": 0.0}
                      for b, pad in self.chart]
        beats = songs.COUNT_IN + songs.length_beats(self.song) + songs.TAIL
        self.end_t = t + beats * self.period
        self.state = "play"

    def update(self, app, t):
        if self.state == "wait":
            if t >= self.start_at:
                self.begin_play(t)
        elif self.state == "play":
            for n in self.notes:
                if n["state"] == "pending" and t - JUDGE_OFFSET - n["t"] > self.window:
                    n["state"], n["done"] = "miss", t
            if t >= self.end_t:
                self.finish(t)
        elif self.state == "pause":
            if t >= self.pause_until:
                self.start_song(t)

    def finish(self, t):
        self.channel = None
        rate = self.hits / len(self.notes) if self.notes else 1.0
        if rate >= SONG_LEVEL_UP:
            self.auto_up()
        elif rate <= SONG_LEVEL_DOWN:
            self.auto_down()
        # Toujours la fete a la fin d'une chanson, quel que soit le score
        self.app.celebrate(t)
        self.song_idx = (self.song_idx + 1) % len(songs.SONGS)
        self.state = "pause"
        self.pause_until = t + SONG_PAUSE

    def on_hit(self, app, pad, t):
        if self.state != "play":
            return
        tj = t - JUDGE_OFFSET
        best = None
        for n in self.notes:
            if n["state"] == "pending" and n["pad"] == pad and abs(tj - n["t"]) <= self.window:
                if best is None or abs(tj - n["t"]) < abs(tj - best["t"]):
                    best = n
        if best is not None:
            best["state"], best["done"] = "hit", t
            self.hits += 1
            app.burst(pad, n=8, stars=True)
            app.gain_energy(ENERGY_HIT, t)

    def soon(self, t):
        return {n["pad"] for n in self.notes
                if n["state"] == "pending" and -self.window <= n["t"] - t <= self.lead * SONG_CUE}

    def draw_back(self, app, surf, t):
        if self.state == "play":
            app.draw_beat_lines(surf, t, self.audio_t0, self.period, self.lead,
                                bar_offset=songs.COUNT_IN)
        else:
            app.draw_beat_lines(surf, t, self.t_scroll, 60.0 / IDLE_SCROLL_BPM, FREE_LEAD)
        # La piste dont la note arrive bientot s'allume : c'est le moment de frapper
        for pad in self.soon(t):
            app.light_lane(surf, pad, 0.22)

    def draw(self, app, surf, t):
        for pad in self.soon(t):
            if pad != "kick":
                app.draw_target(surf, pad, t)
        # Du plus loin au plus proche : les gemmes proches passent devant
        for n in reversed(self.notes):
            if n["state"] == "hit":
                continue
            if n["state"] == "miss":
                fade = (t - n["done"]) / SONG_MISS_FADE
                if fade >= 1.0:
                    continue
            else:
                fade = 0.0
            app.draw_gem(surf, n["pad"], (n["t"] - t) / self.lead, fade)

    def draw_hud(self, app, surf, t):
        if self.state == "play":
            p = (t - self.audio_t0) / (self.end_t - self.audio_t0)
            app.draw_progress(surf, p)

# ---------------------------------------------------------------------------
# Sorties audio et video
# ---------------------------------------------------------------------------

def card_dirs(asound):
    # card0, card1... (pas le fichier "cards")
    try:
        entries = sorted(os.listdir(asound))
    except OSError:
        return []
    return [os.path.join(asound, e) for e in entries
            if e.startswith("card") and e[4:].isdigit()]


def hdmi_audio_cards(asound="/proc/asound"):
    """Cartes ALSA HDMI dont l'ecran accepte l'audio (ELD avec au moins un format)."""
    found = []
    for base in card_dirs(asound):
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
    ids = []
    for base in card_dirs(asound):
        try:
            with open(os.path.join(base, "id")) as f:
                ids.append(f.read().strip())
        except OSError:
            continue
    if not ids:
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
        pygame.display.init()
        return pygame.display.set_mode(RENDER_SIZE)
    # Au boot, le pilote vc4 peut ne pas etre pret quand le service demarre :
    # sous kmsdrm, c'est deja display.init() qui echoue
    deadline = time.monotonic() + DISPLAY_WAIT
    warned = False
    while True:
        try:
            pygame.display.init()
            return pygame.display.set_mode(RENDER_SIZE, pygame.FULLSCREEN | pygame.SCALED)
        except pygame.error as exc:
            if time.monotonic() > deadline:
                raise
            if not warned:
                print("Ecran pas encore pret (%s), nouvel essai..." % exc)
                warned = True
            pygame.display.quit()
            time.sleep(1.0)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class App:
    def __init__(self, window=False, use_kit=True, progress_file=None):
        # Pas de pygame.init() : il ouvrirait l'audio avant le choix de la sortie
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
            self.sounds = build_sounds(pygame, init, loud_gain=VEL_HARD_GAIN)
            t_songs = time.monotonic()
            self.sounds.update(songs.build_song_sounds(pygame, init))
            print("Chansons et accompagnements synthetises en %.1f s" % (time.monotonic() - t_songs))
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
        self.held = {}         # pad ou bouton -> debut de l'appui (boutons de facade)
        self.effects = []      # anneaux
        self.particles = []    # eclats, etoiles et flammes
        self.kick_flash = 0.0
        self.last_hit = now
        self.start_down_at = None
        self.select_down_at = None
        self.shutdown_at = None      # debut du fondu d'extinction
        self.shutdown_sent = False
        self.banner_until = 0.0
        self.level_banner_until = 0.0
        self.energy = 0.0            # jauge "star power" (0 a 1)
        self.star_until = 0.0
        self.excite = 0.0            # agitation de la foule

        # pads.json : mapping du kit et elements desactives (casses, a reparer)
        kit_cfg = None
        self.disabled = set()
        if use_kit:
            if os.path.exists(PADS_FILE):
                with open(PADS_FILE) as f:
                    kit_cfg = json.load(f)
                self.disabled = set(kit_cfg.get("disabled", [])) & set(PADS)
                if self.disabled:
                    print("Elements desactives :", ", ".join(sorted(self.disabled)))
            else:
                print("pads.json absent : lancer map_pads.py. Clavier seulement.")
        # pad -> bouton de facade qui envoie le meme code (detecte par appui long)
        self.button_of = shared_buttons(kit_cfg)

        self.modes = [FreePlay(), CopyMe(), Beat(), Song()]
        self.progress_file = progress_file
        self.load_progress()
        self.mode_idx = 0
        self.mode = self.modes[0]
        self.mode.enter(self, now)
        self.last_t = now
        self.running = True

        self.inp = None
        if kit_cfg is not None:
            self.inp = EvdevInput(kit_cfg, self.kit_event)
            # Le thread kit doit pouvoir reprendre la main vite pendant le rendu
            sys.setswitchinterval(0.001)
            self.inp.start()

    def target_pads(self):
        """Pads que les jeux peuvent demander (hors pedale par defaut, hors desactives)."""
        pads = GAME_PADS + (["kick"] if KICK_IN_GAMES else [])
        return [p for p in pads if p not in self.disabled]

    def reset_clock(self, t):
        # Pour les tests : repart d'un temps synthetique
        self.last_t = t
        self.last_hit = t
        self.mode.leave(self)
        self.mode.enter(self, t)

    # --- niveaux (progress.json) -------------------------------------------

    def load_progress(self):
        if not self.progress_file or not os.path.exists(self.progress_file):
            return
        try:
            with open(self.progress_file) as f:
                levels = json.load(f).get("levels", {})
        except (OSError, ValueError) as exc:
            print("progress.json illisible (%s) : niveaux par defaut" % exc)
            return
        for m in self.modes:
            lv = levels.get(m.key, DEFAULT_LEVEL)
            if isinstance(lv, int):
                m.home = m.level = max(0, min(len(LEVELS) - 1, lv))

    def save_progress(self):
        if not self.progress_file:
            return
        try:
            with open(self.progress_file, "w") as f:
                json.dump({"levels": {m.key: m.home for m in self.modes}}, f, indent=2)
        except OSError as exc:
            print("Niveaux non enregistres (%s)" % exc)

    def change_level(self, delta, t):
        m = self.mode
        new = max(0, min(len(LEVELS) - 1, m.home + delta))
        self.level_banner_until = t + LEVEL_BANNER
        self.banner_until = 0.0
        if new == m.home and new == m.level:
            return
        m.leave(self)
        m.home = m.level = new
        self.effects.clear()
        m.enter(self, t)
        self.play("level_up" if delta > 0 else "level_down", 0.7)
        print("Niveau %s : %s" % (m.key, LEVELS[new]))
        self.save_progress()

    # --- geometrie : autoroute en perspective -------------------------------

    def layout(self, w, h):
        self.w, self.h = w, h
        self.cx = w / 2.0
        self.hw = w * HIGHWAY_WIDTH / 2.0           # demi-largeur a la ligne de frappe
        self.ys = h * STRIKE_Y
        top = h * HIGHWAY_TOP
        # Point de fuite : z = 1 (fond de l'autoroute) tombe sur HIGHWAY_TOP
        self.yv = (top - HIGHWAY_FAR * self.ys) / (1.0 - HIGHWAY_FAR)
        self.persp = 1.0 / HIGHWAY_FAR - 1.0
        s_bottom = (h - self.yv) / (self.ys - self.yv)
        self.z_bottom = (1.0 / s_bottom - 1.0) / self.persp
        self.lane_w = 2.0 * self.hw / len(LANES)
        self.lane_u = {"kick": 0.0}
        self.geo = {}
        for i, pad in enumerate(LANES):
            u = -1.0 + (2 * i + 1) / float(len(LANES))
            self.lane_u[pad] = u
            rx = int(self.lane_w * FRET_SIZE)
            self.geo[pad] = (int(self.cx + u * self.hw), int(self.ys), rx, int(rx * GEM_TILT))
        self.geo["kick"] = (int(self.cx), int(self.ys), int(self.hw * 0.98), int(h * KICK_BAR))
        # Projecteurs (rampe en haut, de chaque cote) et foule (en bas, de chaque cote)
        self.lamps = []
        for side in (-1, 1):
            for i in range(4):
                x = self.cx + side * w * (0.24 + 0.075 * i)
                self.lamps.append((int(x), int(h * 0.17), LANES[(i + (side > 0) * 2) % len(LANES)]))
        rng = random.Random(3)
        self.crowd = []
        for side in (-1, 1):
            for i in range(CROWD_SIZE):
                x = self.cx + side * w * (0.33 + 0.165 * i / max(1, CROWD_SIZE - 1))
                x += rng.uniform(-0.012, 0.012) * w
                self.crowd.append({"x": x, "y": h * rng.uniform(0.95, 1.02),
                                   "r": h * rng.uniform(0.026, 0.034),
                                   "ph": rng.uniform(0, 6.28), "sp": rng.uniform(5.0, 7.0)})
        self.crowd.sort(key=lambda p: p["y"])
        self.bg = self.build_background()

    def project(self, u, z):
        """(u, z) -> (x, y, echelle). u = -1..1 en travers, z = 0 ligne de frappe, 1 fond."""
        s = 1.0 / (1.0 + self.persp * z)
        return self.cx + u * self.hw * s, self.yv + (self.ys - self.yv) * s, s

    def lane_poly(self, u0, u1, z0, z1):
        return [self.project(u0, z1)[:2], self.project(u1, z1)[:2],
                self.project(u1, z0)[:2], self.project(u0, z0)[:2]]

    def build_background(self):
        # Decor fixe pre-rendu une fois : un seul blit par image ensuite
        w, h = self.w, self.h
        bg = pygame.Surface((w, h))
        for y in range(0, h, 4):
            pygame.draw.rect(bg, lerp(STAGE_TOP, STAGE_BOTTOM, y / float(h)), (0, y, w, 4))
        # Faisceaux des projecteurs (couleur pleine tres sombre, pas d'alpha)
        for x, y, pad in self.lamps:
            spread = w * 0.07
            foot = x + (x - self.cx) * 0.35
            pygame.draw.polygon(bg, lerp(STAGE_BOTTOM, PADS[pad]["color"], 0.07),
                                [(x - 6, y), (x + 6, y), (foot + spread, h), (foot - spread, h)])
        # Enceintes de scene
        for side in (-1, 1):
            cw, ch = w * 0.085, h * 0.16
            x = self.cx + side * w * 0.44 - cw / 2
            for k in range(2):
                y = h * 0.30 + k * (ch + h * 0.01)
                pygame.draw.rect(bg, SPEAKER, (x, y, cw, ch), border_radius=6)
                pygame.draw.rect(bg, (50, 46, 66), (x, y, cw, ch), 2, border_radius=6)
                for j in range(2):
                    c = (int(x + cw / 2), int(y + ch * (0.28 + 0.44 * j)))
                    pygame.draw.circle(bg, (12, 12, 18), c, int(ch * 0.19))
                    pygame.draw.circle(bg, (60, 56, 78), c, int(ch * 0.19), 2)
                    pygame.draw.circle(bg, (40, 38, 52), c, int(ch * 0.06))
        # Autoroute : bandes de couleur tres sombres par piste, separateurs
        pygame.draw.polygon(bg, HIGHWAY_COL, self.lane_poly(-1.0, 1.0, self.z_bottom, 1.0))
        d = 1.0 / len(LANES)
        for pad in LANES:
            u = self.lane_u[pad]
            pygame.draw.polygon(bg, lerp(HIGHWAY_COL, PADS[pad]["color"], 0.07),
                                self.lane_poly(u - d * 0.92, u + d * 0.92, self.z_bottom, 1.0))
        for i in range(1, len(LANES)):
            u = -1.0 + 2.0 * i / len(LANES)
            pygame.draw.line(bg, LANE_LINE, self.project(u, 1.0)[:2], self.project(u, self.z_bottom)[:2], 2)
        # Fond de l'autoroute : un horizon lumineux
        x0, y0, _ = self.project(-1.0, 1.0)
        x1, _, _ = self.project(1.0, 1.0)
        pygame.draw.line(bg, (70, 60, 110), (x0, y0), (x1, y0), 3)
        return bg

    # --- audio ------------------------------------------------------------

    def play(self, name, volume=1.0, loops=0):
        s = self.sounds.get(name)
        if s is None:
            return None
        ch = s.play(loops=loops)
        if ch is not None:
            ch.set_volume(volume)
        return ch

    # --- effets -----------------------------------------------------------

    def ring(self, pad, t, strong=True):
        cx, cy, rx, ry = self.geo[pad]
        if pad == "kick":
            rx, ry = int(self.lane_w * FRET_SIZE), int(self.lane_w * FRET_SIZE * GEM_TILT)
        self.effects.append({
            "c": (cx, cy), "rx": rx, "ry": ry, "grow": 1.0 if strong else 0.5,
            "t0": t, "dur": 0.45 if strong else 0.9,
            "col": PADS[pad]["color"], "w": max(3, int(self.h * (0.010 if strong else 0.006))),
        })

    def burst(self, pad, n=12, stars=False):
        cx, cy, rx, ry = self.geo[pad]
        for _ in range(n):
            a = random.uniform(0, 2 * math.pi)
            sp = random.uniform(0.35, 0.8) * self.h
            self.particles.append({
                "x": cx + math.cos(a) * rx * 0.6, "y": cy + math.sin(a) * ry * 0.6,
                "vx": math.cos(a) * sp, "vy": math.sin(a) * sp * 0.6 - 0.35 * self.h,
                "life": 0.0, "max": random.uniform(0.5, 0.9), "grav": 1.0,
                "col": PADS[pad]["color"] if not stars else (255, 225, 80),
                "size": self.h * (0.022 if stars else 0.012), "star": stars,
                "rot": random.uniform(0, 6.28), "spin": random.uniform(-6, 6),
            })
        del self.particles[:-MAX_PARTICLES]

    def flame(self, pad):
        # Flamme Guitar Hero au-dessus de la cible frappee : monte et s'eteint vite
        cx, cy, rx, ry = self.geo[pad]
        hot = [(255, 240, 180), (255, 200, 60), (255, 140, 30), PADS[pad]["color"]]
        for _ in range(9):
            self.particles.append({
                "x": cx + random.uniform(-0.6, 0.6) * rx, "y": cy - ry * 0.3,
                "vx": random.uniform(-0.08, 0.08) * self.h, "vy": -random.uniform(0.5, 0.9) * self.h,
                "life": 0.0, "max": random.uniform(0.18, 0.32), "grav": -0.6,
                "col": random.choice(hot), "size": self.h * random.uniform(0.018, 0.03),
                "star": False, "rot": 0.0, "spin": 0.0,
            })
        del self.particles[:-MAX_PARTICLES]

    def hit_visual(self, pad, t):
        self.glow[pad] = 1.0
        self.excite = min(1.0, self.excite + 0.12)
        if pad == "kick":
            self.kick_flash = 1.0
        else:
            self.ring(pad, t)
            self.flame(pad)
            self.burst(pad, n=6)

    def demo(self, pad, t):
        # Le jeu "montre" un pad (son + lumiere) sans que l'enfant ait frappe
        self.play(PADS[pad]["sound"])
        self.hit_visual(pad, t)

    def reward(self, pad, t):
        self.play("sparkle")
        self.burst(pad, n=10, stars=True)
        self.gain_energy(ENERGY_HIT, t)

    def gain_energy(self, amount, t):
        # La jauge ne fait que monter ; pleine, elle declenche le star power
        if t < self.star_until:
            return
        self.energy = min(1.0, self.energy + amount)
        if self.energy >= 1.0:
            self.energy = 0.0
            self.star_until = t + STAR_TIME
            self.excite = 1.0
            self.play("star", 0.8)
            self.play("cheer", 0.6)

    def star_power(self, t):
        return t < self.star_until

    def celebrate(self, t):
        self.play("jingle")
        self.play("cheer", 0.5)
        self.excite = 1.0
        for _ in range(45):
            a = random.uniform(0, 2 * math.pi)
            sp = random.uniform(0.3, 1.0) * self.h
            self.particles.append({
                "x": self.w / 2, "y": self.h * 0.45,
                "vx": math.cos(a) * sp, "vy": math.sin(a) * sp - 0.3 * self.h,
                "life": 0.0, "max": random.uniform(1.0, 1.6), "grav": 1.0,
                "col": random.choice([PADS[p]["color"] for p in GAME_PADS] + [(255, 255, 255)]),
                "size": self.h * random.uniform(0.025, 0.045), "star": True,
                "rot": random.uniform(0, 6.28), "spin": random.uniform(-5, 5),
            })
        del self.particles[:-MAX_PARTICLES]

    # --- entree -----------------------------------------------------------
    #
    # Une frappe = strike() (debounce + son, tout de suite, n'importe quel
    # thread) puis react() (visuel + logique du mode, thread principal).

    def strike(self, kind, name, t, vel=None):
        """Retourne ce que react() doit faire de l'evenement, ou None.

        "hit" : frappe sonnee ; "hold" : suivi d'appui seulement (rebond, pad
        desactive qui sert de bouton) ; "up" et "ctl" : relachement, controles.
        vel : force brute du coup (rapport HID) ou None si inconnue.
        """
        if name not in PADS:
            return "ctl" if name in CONTROLS else None
        if kind != "down":
            return "up"
        if name in self.disabled:
            return "hold" if name in self.button_of else None
        with self.strike_lock:
            if t - self.last_down[name] < DEBOUNCE:
                return "hold"
            self.last_down[name] = t
        self.play_pad(name, velocity_gain(vel))
        return "hit"

    def play_pad(self, pad, gain):
        # Au-dela du son normal (gain > 1), le volume d'un canal plafonne a 1.0 :
        # on passe a la variante "_loud" pre-saturee, jouee au volume qui donne ce gain
        name = PADS[pad]["sound"]
        loud = sounds.LOUD_ACHIEVED.get(name)
        if gain > 1.0 and loud and (name + "_loud") in self.sounds:
            self.play(name + "_loud", min(1.0, gain / loud))
        else:
            self.play(name, min(1.0, gain))

    def react(self, kind, name, t, how="hit"):
        if name == "start":
            self.start_down_at = t if kind == "down" else None
            return
        if name == "select":
            self.select_down_at = t if kind == "down" else None
            return
        if kind != "down":
            self.held.pop(name, None)
            return
        if name in BUTTON_PADS or name in self.button_of:
            self.held[name] = t
        if how != "hit" or name in BUTTON_PADS or self.shutdown_at is not None:
            return
        self.last_hit = t
        self.hit_visual(name, t)
        self.mode.on_hit(self, name, t)

    def handle(self, kind, name, t):
        # Point d'entree unique (clavier, tests) : son puis reaction
        how = self.strike(kind, name, t)
        if how:
            self.react(kind, name, t, how)

    def kit_event(self, kind, name, t, vel=None):
        # Appele depuis le thread kit
        how = self.strike(kind, name, t, vel)
        if how:
            self.kit_queue.append((kind, name, t, how))

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

    def switch_mode(self, t, step=1):
        self.mode.leave(self)
        self.mode_idx = (self.mode_idx + step) % len(self.modes)
        self.mode = self.modes[self.mode_idx]
        self.mode.level = self.mode.home
        self.effects.clear()
        self.mode.enter(self, t)
        self.banner_until = t + 1.5
        self.level_banner_until = 0.0
        self.play("jingle", 0.5)

    def held_buttons(self, t):
        """[(bouton, progression 0-1)] des boutons de facade en cours d'appui."""
        out = []
        for name, t0 in self.held.items():
            button = name if name in BUTTON_PADS else self.button_of.get(name)
            if button:
                out.append((button, (t - t0) / BUTTON_HOLD))
        return out

    def button_action(self, button, t):
        action = BUTTON_ACTIONS.get(button)
        if action == "level_up":
            self.change_level(1, t)
        elif action == "level_down":
            self.change_level(-1, t)
        elif action == "next_mode":
            self.switch_mode(t, 1)
        elif action == "prev_mode":
            self.switch_mode(t, -1)

    # --- extinction (SELECT + START maintenus) ------------------------------

    def combo_since(self):
        if self.start_down_at is None or self.select_down_at is None:
            return None
        return max(self.start_down_at, self.select_down_at)

    def begin_shutdown(self, t):
        print("Extinction demandee (SELECT + START)")
        self.shutdown_at = t
        self.start_down_at = self.select_down_at = None
        self.held.clear()
        self.mode.leave(self)
        self.play("bye")

    def request_poweroff(self):
        try:
            res = subprocess.run(SHUTDOWN_CMD, timeout=10,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            ok = res.returncode == 0
            detail = res.stdout.decode(errors="replace").strip() or "code %d" % res.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            ok, detail = False, str(exc)
        if ok:
            print("Extinction en cours")
            return True
        # Aucun retour negatif a l'ecran : on reprend simplement le jeu
        print("Extinction refusee (%s) : regle sudoers installee ? voir README" % detail)
        self.shutdown_at = None
        self.shutdown_sent = False
        return False

    # --- boucle -----------------------------------------------------------

    def update(self, t, dt):
        if self.shutdown_at is not None:
            if not self.shutdown_sent and t - self.shutdown_at >= SHUTDOWN_FADE:
                self.shutdown_sent = True
                self.request_poweroff()
            return
        combo = self.combo_since()
        if combo is not None and t - combo >= SHUTDOWN_HOLD:
            self.begin_shutdown(t)
            return
        # SELECT tenu : START prepare l'extinction, pas un changement de mode
        if (self.start_down_at is not None and self.select_down_at is None
                and t - self.start_down_at >= HOLD_TO_SWITCH):
            self.start_down_at = None
            self.switch_mode(t)
        # Bouton de facade maintenu : une seule action par appui
        for button, p in self.held_buttons(t):
            if p >= 1.0 and self.select_down_at is None:
                for name in [n for n in self.held if n == button or self.button_of.get(n) == button]:
                    del self.held[name]
                self.button_action(button, t)
                break
        k = math.exp(-dt / 0.22)
        for p in self.glow:
            self.glow[p] *= k
        self.kick_flash *= math.exp(-dt / 0.15)
        self.excite *= math.exp(-dt / 1.5)
        if self.star_power(t):
            self.excite = max(self.excite, 0.8)
        self.effects = [e for e in self.effects if t - e["t0"] < e["dur"]]
        g = 0.9 * self.h
        alive = []
        for p in self.particles:
            p["life"] += dt
            if p["life"] < p["max"]:
                p["x"] += p["vx"] * dt
                p["y"] += p["vy"] * dt
                p["vy"] += g * p["grav"] * dt
                p["vx"] *= 0.985
                p["rot"] += p["spin"] * dt
                alive.append(p)
        self.particles = alive
        self.mode.update(self, t)

    # --- dessin : autoroute ---------------------------------------------------

    def light_lane(self, surf, pad, k):
        # Piste allumee de la ligne de frappe jusqu'au fond (cible a venir)
        if pad == "kick" or k <= 0.01:
            return
        u, d = self.lane_u[pad], 1.0 / len(LANES)
        pygame.draw.polygon(surf, lerp(HIGHWAY_COL, PADS[pad]["color"], k),
                            self.lane_poly(u - d * 0.92, u + d * 0.92, 0.0, 1.0))

    def draw_beat_lines(self, surf, t, t0, period, lead, bar_offset=0):
        """Lignes de temps qui descendent l'autoroute ; plus epaisses sur la mesure."""
        k0 = math.ceil((t + self.z_bottom * lead - t0) / period)
        k1 = math.floor((t + lead - t0) / period)
        for k in range(k0, k1 + 1):
            z = (t0 + k * period - t) / lead
            x0, y, s = self.project(-1.0, z)
            x1, _, _ = self.project(1.0, z)
            bar = (k - bar_offset) % 4 == 0
            col = lerp(HIGHWAY_COL, (110, 105, 150) if bar else (70, 68, 96), 1.0 - 0.7 * max(0.0, z))
            pygame.draw.line(surf, col, (x0, y), (x1, y), max(1, int((4 if bar else 2) * s)))

    def draw_rails(self, surf, t):
        # Bords de l'autoroute : s'echauffent avec l'energie, bleus en star power
        if self.star_power(t):
            col = lerp(STAR_BLUE, WHITE, 0.3 + 0.3 * math.sin(t * 8.0))
        else:
            col = lerp(RAIL, RAIL_HOT, self.energy)
        for u in (-1.0, 1.0):
            pygame.draw.line(surf, col, self.project(u, 1.0)[:2], self.project(u, self.z_bottom)[:2],
                             max(3, int(self.h * 0.008)))

    def draw_fret(self, surf, pad):
        # Cible sur la ligne de frappe : anneau de couleur, s'allume a la frappe
        cx, cy, rx, ry = self.geo[pad]
        base = PADS[pad]["color"]
        g = self.glow[pad]
        fill = lerp(DARK_FILL, base, 0.15 + 0.85 * g)
        rim = lerp(base, WHITE, 0.6 * g)
        if pad == "kick":
            g = max(g, self.kick_flash)
            rect = pygame.Rect(0, 0, 2 * rx, 2 * ry)
            rect.center = (cx, cy)
            pygame.draw.rect(surf, lerp(lerp(DARK_FILL, base, 0.35), WHITE, 0.5 * g), rect, border_radius=ry)
            pygame.draw.rect(surf, lerp(base, WHITE, 0.6 * g), rect, max(2, ry // 4), border_radius=ry)
            return
        s = 1.0 + 0.08 * g
        rect = pygame.Rect(0, 0, int(2 * rx * s), int(2 * ry * s))
        rect.center = (cx, cy)
        side = rect.move(0, max(3, int(ry * 0.25)))
        pygame.draw.ellipse(surf, lerp(base, BLACK, 0.6), side)
        pygame.draw.ellipse(surf, fill, rect)
        pygame.draw.ellipse(surf, rim, rect, max(4, int(rx * 0.13)))
        inner = rect.inflate(-int(rx * 0.75), -int(ry * 0.75))
        if PADS[pad]["shape"] == "cymbal":
            # Cymbale : sillons concentriques et cloche au centre
            pygame.draw.ellipse(surf, lerp(rim, fill, 0.5), inner, 2)
            bell = rect.inflate(-int(rx * 1.4), -int(ry * 1.4))
            pygame.draw.ellipse(surf, lerp(base, WHITE, 0.3 + 0.5 * g), bell)
        else:
            pygame.draw.ellipse(surf, lerp(fill, BLACK, 0.35 * (1.0 - g)), inner)

    def draw_gem(self, surf, pad, z, fade=0.0):
        """Gemme a la profondeur z (1 = fond de l'autoroute, 0 = ligne de frappe)."""
        if z > 1.0 or z < self.z_bottom:
            return
        if z > 0.85:
            fade = max(fade, (z - 0.85) / 0.15)       # sort doucement du fond
        color = PADS[pad]["color"]
        col = lerp(color, HIGHWAY_COL, fade)
        rim = lerp(WHITE, HIGHWAY_COL, fade)
        x, y, s = self.project(self.lane_u[pad], z)
        if pad == "kick":
            # Pedale : barre en travers de l'autoroute, comme dans Guitar Hero
            x0 = self.project(-0.97, z)[0]
            hh = max(3, int(self.h * KICK_BAR * 0.9 * s))
            rect = pygame.Rect(int(x0), int(y - hh / 2), int(2 * (x - x0)), hh)
            pygame.draw.rect(surf, col, rect, border_radius=hh // 2)
            pygame.draw.rect(surf, rim, rect, max(1, hh // 4), border_radius=hh // 2)
            return
        rx = max(2.0, self.lane_w * GEM_SIZE * s)
        ry = max(1.0, rx * GEM_TILT * (0.8 if PADS[pad]["shape"] == "cymbal" else 1.0))
        rect = pygame.Rect(0, 0, int(2 * rx), int(2 * ry))
        rect.center = (int(x), int(y))
        pygame.draw.ellipse(surf, lerp(col, BLACK, 0.5), rect.move(0, max(2, int(ry * 0.4))))
        pygame.draw.ellipse(surf, col, rect)
        pygame.draw.ellipse(surf, rim, rect, max(2, int(rx * 0.14)))
        if PADS[pad]["shape"] == "cymbal":
            bell = rect.inflate(-int(rx * 1.1), -int(ry * 1.0))
            bell.centery -= int(ry * 0.2)
            if bell.width > 2 and bell.height > 2:
                pygame.draw.ellipse(surf, lerp(col, WHITE, 0.55), bell)
        else:
            inner = rect.inflate(-int(rx * 0.9), -int(ry * 0.9))
            if inner.width > 2 and inner.height > 2:
                pygame.draw.ellipse(surf, lerp(col, BLACK, 0.3), inner)

    def draw_target(self, surf, pad, t):
        # Anneau blanc pulsant autour de la cible attendue
        cx, cy, rx, ry = self.geo[pad]
        pulse = 0.5 + 0.5 * math.sin(t * 6.0)
        grow = 1.18 + 0.12 * pulse
        col = lerp(PADS[pad]["color"], WHITE, 0.4 + 0.6 * pulse)
        rect = pygame.Rect(0, 0, int(2 * rx * grow), int(2 * ry * grow))
        rect.center = (cx, cy)
        pygame.draw.ellipse(surf, col, rect, max(4, int(self.h * 0.009)))

    # --- dessin : scene -------------------------------------------------------

    def draw_lamps(self, surf, t):
        star = self.star_power(t)
        pygame.draw.line(surf, (50, 46, 66), (self.lamps[0][0] - 20, self.lamps[0][1] - 14),
                         (self.lamps[3][0] + 20, self.lamps[3][1] - 14), 4)
        pygame.draw.line(surf, (50, 46, 66), (self.lamps[4][0] - 20, self.lamps[4][1] - 14),
                         (self.lamps[7][0] + 20, self.lamps[7][1] - 14), 4)
        r = int(self.h * 0.016)
        for i, (x, y, pad) in enumerate(self.lamps):
            on = self.glow[pad]
            if star:
                on = max(on, 0.6 + 0.4 * math.sin(t * 7.0 + i))
            col = lerp((60, 56, 76), lerp(PADS[pad]["color"], WHITE, 0.4), 0.2 + 0.8 * on)
            pygame.draw.circle(surf, col, (x, y), r)
            if on > 0.3:
                pygame.draw.circle(surf, lerp(STAGE_TOP, PADS[pad]["color"], on), (x, y), int(r * 1.9), 2)

    def draw_crowd(self, surf, t):
        star = self.star_power(t)
        jump = self.excite
        for i, p in enumerate(self.crowd):
            bounce = abs(math.sin(t * p["sp"] + p["ph"])) * p["r"] * (0.15 + 0.9 * jump)
            x, r = int(p["x"]), p["r"]
            y = p["y"] - bounce
            col = CROWD_COL
            if star:
                col = lerp(CROWD_COL, PADS[LANES[i % len(LANES)]]["color"], 0.35)
            # Bras leves quand la foule s'enflamme
            if jump > 0.45:
                wave = math.sin(t * 9.0 + p["ph"]) * r * 0.4
                for side in (-1, 1):
                    pygame.draw.line(surf, col, (x + side * r * 0.9, y - r * 1.2),
                                     (x + side * r * 1.4 + wave, y - r * 3.1), max(3, int(r * 0.35)))
            body = pygame.Rect(int(x - r * 1.15), int(y - r * 1.6), int(r * 2.3), int(r * 3.0))
            pygame.draw.ellipse(surf, col, body)
            pygame.draw.circle(surf, col, (x, int(y - r * 2.1)), int(r * 0.85))

    # --- dessin : interface sans texte ---------------------------------------

    def draw_pips(self, surf, total, done):
        # Progression : une petite gemme par reussite attendue, en haut au centre
        gap = self.h * 0.042
        x0 = self.w / 2 - gap * (total - 1) / 2
        r = int(self.h * 0.014)
        for i in range(total):
            c = (int(x0 + i * gap), int(self.h * 0.055))
            if i < done:
                pygame.draw.circle(surf, (250, 215, 60), c, r)
                pygame.draw.circle(surf, WHITE, c, r, 2)
            else:
                pygame.draw.circle(surf, DARK_FILL, c, r)
                pygame.draw.circle(surf, (80, 80, 100), c, r, 2)

    def draw_progress(self, surf, p):
        p = max(0.0, min(1.0, p))
        x, y, w = int(self.w * 0.33), int(self.h * 0.045), int(self.w * 0.34)
        pygame.draw.rect(surf, DARK_FILL, (x, y, w, 8), border_radius=4)
        pygame.draw.rect(surf, (200, 200, 220), (x, y, int(w * p), 8), border_radius=4)

    def draw_energy(self, surf, t):
        # Jauge verticale facon star power, a droite de l'autoroute
        star = self.star_power(t)
        cw, ch = int(self.h * 0.032), int(self.h * 0.034)
        x = int(self.cx + self.w * 0.255)
        y_bottom = int(self.h * 0.62)
        lit = ENERGY_CELLS if star else int(self.energy * ENERGY_CELLS + 1e-6)
        for i in range(ENERGY_CELLS):
            rect = pygame.Rect(x, y_bottom - (i + 1) * (ch + 4), cw, ch)
            if i < lit:
                if star:
                    col = lerp(STAR_BLUE, WHITE, 0.5 + 0.5 * math.sin(t * 8.0 - i * 0.6))
                else:
                    col = lerp((80, 200, 255), RAIL_HOT, i / float(ENERGY_CELLS - 1))
                pygame.draw.rect(surf, col, rect, border_radius=5)
            else:
                pygame.draw.rect(surf, DARK_FILL, rect, border_radius=5)
            pygame.draw.rect(surf, (90, 90, 120), rect, 2, border_radius=5)

    def draw_steps(self, surf, x, y, unit, mode):
        """Escalier des niveaux : une marche par niveau, pleines jusqu'au niveau joue."""
        for i in range(len(LEVELS)):
            bh = int(unit * (0.6 + 0.5 * i))
            rect = pygame.Rect(int(x + i * unit * 1.35), int(y - bh), int(unit), bh)
            if i <= mode.level:
                pygame.draw.rect(surf, LEVEL_COLORS[i], rect, border_radius=max(2, int(unit // 5)))
            else:
                pygame.draw.rect(surf, DARK_FILL, rect, border_radius=max(2, int(unit // 5)))
                pygame.draw.rect(surf, (80, 80, 100), rect, 2, border_radius=max(2, int(unit // 5)))
            if i == mode.home and mode.home != mode.level:
                # Niveau choisi par le parent, pas encore rejoint : contour blanc
                pygame.draw.rect(surf, WHITE, rect, 2, border_radius=max(2, int(unit // 5)))

    def draw_symbol(self, surf, button, cx, cy, r, col):
        w = max(3, int(r * 0.22))
        if button == "triangle":
            pts = [(cx, cy - r), (cx + 0.87 * r, cy + 0.5 * r), (cx - 0.87 * r, cy + 0.5 * r)]
            pygame.draw.polygon(surf, col, pts, w)
        elif button == "circle":
            pygame.draw.circle(surf, col, (cx, cy), int(r * 0.85), w)
        elif button == "cross":
            d = r * 0.7
            pygame.draw.line(surf, col, (cx - d, cy - d), (cx + d, cy + d), w)
            pygame.draw.line(surf, col, (cx - d, cy + d), (cx + d, cy - d), w)
        else:
            d = int(r * 0.72)
            pygame.draw.rect(surf, col, (cx - d, cy - d, 2 * d, 2 * d), w)

    def draw_hud(self, surf, t):
        # Coin superieur gauche : escalier des niveaux du mode
        unit = self.h * 0.022
        self.draw_steps(surf, self.h * 0.04, self.h * 0.11, unit, self.mode)
        # Coin superieur droit : un point par mode, le mode actif est plein
        r = int(self.h * 0.012)
        for i in range(len(self.modes)):
            x = int(self.w - self.h * 0.04 - (len(self.modes) - 1 - i) * r * 3)
            y = int(self.h * 0.04)
            if i == self.mode_idx:
                pygame.draw.circle(surf, (200, 200, 220), (x, y), r)
            else:
                pygame.draw.circle(surf, (80, 80, 100), (x, y), r, 2)
        # Jauge d'appui long : START seul (blanc), SELECT + START (bleu nuit),
        # bouton de facade (couleur de son pad, symbole au centre)
        combo = self.combo_since()
        button = None
        if combo is not None:
            p, col = (t - combo) / SHUTDOWN_HOLD, SHUTDOWN_GAUGE
        elif self.start_down_at is not None and self.select_down_at is None:
            p, col = (t - self.start_down_at) / HOLD_TO_SWITCH, WHITE
        else:
            held = self.held_buttons(t)
            if held:
                button, p = max(held, key=lambda b: b[1])
                col = PADS[BUTTON_PADS[button]]["color"]
            else:
                p = 0.0
        if p > 0.15:
            rr = int(self.h * 0.035)
            cx, cy = int(self.w - self.h * 0.06), int(self.h * 0.11)
            rect = pygame.Rect(cx - rr, cy - rr, 2 * rr, 2 * rr)
            pygame.draw.arc(surf, col, rect, math.pi / 2, math.pi / 2 + 2 * math.pi * min(1.0, p), 5)
            if button:
                self.draw_symbol(surf, button, cx, cy, rr * 0.55, col)
        self.draw_energy(surf, t)
        self.mode.draw_hud(self, surf, t)
        # Banniere de changement de mode : N gros points au-dessus de l'autoroute
        if t < self.banner_until:
            n = self.mode.dots
            r = int(self.h * 0.05)
            x0 = self.w / 2 - (n - 1) * r * 1.5
            back = pygame.Rect(0, 0, int(n * r * 3 + r), int(r * 3))
            back.center = (int(self.w / 2), int(self.h * 0.32))
            pygame.draw.rect(surf, DARK_FILL, back, border_radius=r)
            for i in range(n):
                pygame.draw.circle(surf, WHITE, (int(x0 + i * r * 3), int(self.h * 0.32)), r)
        # Banniere de niveau : grand escalier au centre
        if t < self.level_banner_until:
            unit = self.h * 0.06
            width = unit * (1.35 * (len(LEVELS) - 1) + 1)
            back = pygame.Rect(0, 0, int(width + unit), int(unit * 4.2))
            back.center = (int(self.w / 2), int(self.h * 0.42))
            pygame.draw.rect(surf, DARK_FILL, back, border_radius=int(unit * 0.4))
            pygame.draw.rect(surf, LEVEL_COLORS[self.mode.level], back, 3, border_radius=int(unit * 0.4))
            self.draw_steps(surf, self.w / 2 - width / 2, back.bottom - unit * 0.5, unit, self.mode)

    def draw_shutdown(self, surf, t):
        # Fondu "televiseur qui s'eteint" : un rond blanc qui retrecit sur fond noir
        p = min(1.0, (t - self.shutdown_at) / SHUTDOWN_FADE)
        surf.fill(lerp(BG, (0, 0, 0), p))
        r = int(self.h * 0.12 * (1.0 - p) ** 2)
        if r > 0:
            pygame.draw.circle(surf, lerp(WHITE, BG, p), (self.w // 2, self.h // 2), r)

    def draw(self, t):
        surf = self.screen
        if self.shutdown_at is not None:
            self.draw_shutdown(surf, t)
            return
        surf.blit(self.bg, (0, 0))
        self.draw_lamps(surf, t)
        # Pistes eclairees par les frappes recentes
        for pad in LANES:
            if self.glow[pad] > 0.05 and pad not in self.disabled:
                self.light_lane(surf, pad, 0.3 * self.glow[pad])
        self.mode.draw_back(self, surf, t)
        self.draw_rails(surf, t)
        for e in self.effects:
            p = (t - e["t0"]) / e["dur"]
            ease = 1 - (1 - p) ** 2
            s = 1.0 + e["grow"] * ease
            rect = pygame.Rect(0, 0, int(2 * e["rx"] * s), int(2 * e["ry"] * s))
            rect.center = e["c"]
            pygame.draw.ellipse(surf, lerp(e["col"], HIGHWAY_COL, p), rect, e["w"])
        if "kick" not in self.disabled:
            self.draw_fret(surf, "kick")
        for pad in LANES:
            if pad not in self.disabled:
                self.draw_fret(surf, pad)
        self.mode.draw(self, surf, t)
        for p in self.particles:
            fade = p["life"] / p["max"]
            col = lerp(p["col"], BG, fade ** 2)
            size = p["size"] * (1 - 0.6 * fade)
            if p["star"]:
                pygame.draw.polygon(surf, col, star_points(p["x"], p["y"], size, p["rot"]))
            else:
                pygame.draw.circle(surf, col, (int(p["x"]), int(p["y"])), max(1, int(size)))
        self.draw_crowd(surf, t)
        self.draw_hud(surf, t)

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
            self.mode.leave(self)
            if self.inp is not None:
                self.inp.stop()
            pygame.quit()


def main():
    ap = argparse.ArgumentParser(description="PiDrum")
    ap.add_argument("--window", action="store_true", help="fenetre 1280x720 au lieu du plein ecran")
    ap.add_argument("--keyboard-only", action="store_true", help="ignorer le kit, clavier seulement")
    args = ap.parse_args()
    App(window=args.window, use_kit=not args.keyboard_only, progress_file=PROGRESS_FILE).run()


if __name__ == "__main__":
    sys.exit(main())
