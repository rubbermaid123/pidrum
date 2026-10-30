# PiDrum - synthese des sons de batterie (aucun fichier audio requis)
#
# Tous les sons sont generes avec numpy au demarrage. Pour utiliser de vrais
# echantillons plus tard, il suffit de remplacer build_sounds() par un
# chargement de fichiers .wav (pygame.mixer.Sound("fichier.wav")).

import numpy as np

SNARE_LP_TAPS = 5       # passe-bas des fils de caisse claire (plus grand = plus sourd)
SNARE_WIRES = 0.7       # part des fils face au corps de la peau
SNARE_DRIVE = 2.0       # saturation douce (tanh) : 1 = aucune
SNARE_BODY_DECAY = 0.11 # (s) resonance de la peau : plus long = plus de rondeur
SNARE_THUMP = 0.8       # impact grave (~140 -> 95 Hz) au moment de la frappe


class Synth:
    def __init__(self, sr=44100, seed=7):
        self.sr = sr
        self.rng = np.random.default_rng(seed)

    def t(self, dur):
        return np.arange(int(self.sr * dur)) / self.sr

    def env(self, dur, decay, attack=0.0):
        t = self.t(dur)
        e = np.exp(-t / decay)
        if attack > 0:
            e *= np.clip(t / attack, 0.0, 1.0)
        return e

    def sweep(self, f0, f1, dur, tau):
        # Sinus dont la frequence glisse de f0 vers f1 (constante de temps tau)
        t = self.t(dur)
        f = f1 + (f0 - f1) * np.exp(-t / tau)
        return np.sin(2 * np.pi * np.cumsum(f) / self.sr)

    def tone(self, freq, dur):
        return np.sin(2 * np.pi * freq * self.t(dur))

    def noise(self, dur):
        return self.rng.uniform(-1.0, 1.0, int(self.sr * dur))

    @staticmethod
    def hp(x, passes=1):
        # Passe-haut grossier par differences successives
        for _ in range(passes):
            x = np.diff(x, prepend=0.0)
        return x

    @staticmethod
    def lp(x, k):
        # Passe-bas grossier par moyenne glissante
        if k <= 1:
            return x
        return np.convolve(x, np.ones(k) / k, mode="same")

    def metal(self, dur, mult=1.0):
        # Ondes carrees inharmoniques (approche TR-808) pour cymbales
        t = self.t(dur)
        out = np.zeros_like(t)
        for f in (205.3, 304.4, 369.6, 522.7, 540.0, 800.0):
            out += np.sign(np.sin(2 * np.pi * f * mult * t))
        return out / 6.0

    @staticmethod
    def norm(x, gain=1.0):
        peak = np.max(np.abs(x))
        if peak > 0:
            x = x / peak
        return x * gain

    @staticmethod
    def fade_out(x, sr, ms=5.0):
        # Evite le "clic" quand l'enveloppe n'est pas tout a fait a zero en fin de son
        n = min(len(x), int(sr * ms / 1000.0))
        if n > 0:
            x = x.copy()
            x[-n:] *= np.linspace(1.0, 0.0, n)
        return x

    # --- Batterie -----------------------------------------------------

    def kick(self):
        d = 0.5
        body = self.sweep(160, 45, d, 0.04) * self.env(d, 0.18)
        click = np.zeros_like(body)
        n = int(self.sr * 0.004)
        click[:n] = self.noise(0.004) * 0.5
        return self.norm(body + click, 1.0)

    def snare(self):
        # Caisse claire : corps (2 modes de la peau), claquement, timbre en bande 1-8 kHz.
        # Le bruit blanc filtre seulement en passe-haut sonnait comme la charleston.
        d = 0.4
        body = (self.sweep(210, 175, d, 0.025) * self.env(d, SNARE_BODY_DECAY)
                + 0.45 * self.sweep(350, 320, d, 0.02) * self.env(d, 0.05))
        thump = self.sweep(140, 95, d, 0.02) * self.env(d, 0.035) * SNARE_THUMP
        crack = np.zeros(int(self.sr * d))
        n = int(self.sr * 0.004)
        crack[:n] = self.noise(0.004) * np.linspace(1.0, 0.0, n)
        wires = self.hp(self.lp(self.noise(d), SNARE_LP_TAPS))
        wires = self.norm(wires) * self.env(d, 0.12, attack=0.001)
        x = self.norm(body + thump + 0.4 * crack + SNARE_WIRES * wires)
        # Saturation douce : remonte le corps (le pic venait du claquement), plus de punch
        return self.norm(np.tanh(SNARE_DRIVE * x), 0.95)

    def tom_hi(self):
        d = 0.45
        body = self.sweep(260, 170, d, 0.08) * self.env(d, 0.18)
        stick = self.lp(self.noise(d), 4) * self.env(d, 0.015) * 0.3
        return self.norm(body + stick, 0.9)

    def tom_lo(self):
        d = 0.6
        body = self.sweep(170, 95, d, 0.10) * self.env(d, 0.25)
        stick = self.lp(self.noise(d), 4) * self.env(d, 0.015) * 0.3
        return self.norm(body + stick, 0.95)

    def hihat(self):
        d = 0.15
        x = self.hp(self.metal(d, 1.6) * 0.6 + self.noise(d) * 0.5, 2)
        return self.norm(x * self.env(d, 0.035), 0.6)

    def crash(self):
        d = 1.8
        x = self.hp(self.metal(d, 1.1) * 0.5 + self.noise(d), 2)
        return self.norm(x * self.env(d, 0.55, attack=0.003), 0.65)

    # --- Interface ----------------------------------------------------

    def jingle(self):
        # Petite fanfare de reussite (do-mi-sol-do)
        parts = []
        notes = (523.25, 659.25, 783.99, 1046.5)
        for i, f in enumerate(notes):
            d = 0.45 if i == len(notes) - 1 else 0.12
            x = self.tone(f, d) + 0.3 * self.tone(2 * f, d) + 0.1 * self.tone(3 * f, d)
            x *= self.env(d, 0.25 if i == len(notes) - 1 else 0.08, attack=0.005)
            parts.append(x)
        return self.norm(np.concatenate(parts), 0.55)

    def bye(self):
        # Au revoir avant l'extinction : la fanfare a l'envers, plus douce (do-sol-mi-do)
        parts = []
        notes = (1046.5, 783.99, 659.25, 523.25)
        for i, f in enumerate(notes):
            d = 0.7 if i == len(notes) - 1 else 0.2
            x = self.tone(f, d) + 0.2 * self.tone(2 * f, d)
            x *= self.env(d, 0.35 if i == len(notes) - 1 else 0.12, attack=0.01)
            parts.append(x)
        return self.norm(np.concatenate(parts), 0.45)

    def sparkle(self):
        # Petit "ding" de bonne frappe
        d = 0.25
        x = self.tone(1318.5, d) + 0.4 * self.tone(1975.5, d)
        return self.norm(x * self.env(d, 0.07, attack=0.002), 0.3)

    def level(self, up=True):
        # Changement de niveau (parent) : deux notes qui montent ou descendent, douces
        notes = (659.25, 987.77) if up else (987.77, 659.25)
        parts = []
        for i, f in enumerate(notes):
            d = 0.22 if i else 0.1
            x = self.tone(f, d) + 0.25 * self.tone(2 * f, d)
            parts.append(x * self.env(d, 0.12 if i else 0.06, attack=0.004))
        return self.norm(np.concatenate(parts), 0.4)

    def star(self):
        # Star power : glissando montant scintillant
        d = 0.9
        t = self.t(d)
        f = 400.0 * 2 ** (2.5 * t / d)
        x = np.sin(2 * np.pi * np.cumsum(f) / self.sr)
        x += 0.5 * np.sin(2 * np.pi * np.cumsum(2 * f) / self.sr)
        x *= 0.6 + 0.4 * np.sin(2 * np.pi * 18.0 * t)        # scintillement
        return self.norm(x * self.env(d, 0.5, attack=0.03), 0.45)

    def cheer(self):
        # Foule qui applaudit : bruit filtre, attaque lente, quelques "ouais" aigus
        d = 1.8
        x = self.hp(self.lp(self.noise(d), 6), 1)
        claps = self.noise(d) * (self.rng.uniform(0, 1, int(self.sr * d)) > 0.9985)
        x = self.norm(x) + 0.6 * self.lp(claps, 30)
        t = self.t(d)
        for f in (520.0, 610.0, 700.0):
            x += 0.08 * np.sin(2 * np.pi * (f + 40 * np.sin(2 * np.pi * 3 * t)) * t)
        e = np.clip(t / 0.25, 0, 1) * np.exp(-np.maximum(0, t - 0.4) / 0.6)
        return self.norm(x * e, 0.35)

    def tick(self, accent=False):
        d = 0.04
        f = 2000.0 if accent else 1500.0
        return self.norm(self.tone(f, d) * self.env(d, 0.008), 0.45 if accent else 0.3)


# nom du son -> (methode de Synth, arguments)
SOUND_NAMES = {
    "kick": ("kick", ()),
    "snare": ("snare", ()),
    "tom_hi": ("tom_hi", ()),
    "tom_lo": ("tom_lo", ()),
    "hihat": ("hihat", ()),
    "crash": ("crash", ()),
    "jingle": ("jingle", ()),
    "bye": ("bye", ()),
    "sparkle": ("sparkle", ()),
    "level_up": ("level", (True,)),
    "level_down": ("level", (False,)),
    "star": ("star", ()),
    "cheer": ("cheer", ()),
    "tick": ("tick", (False,)),
    "tick_accent": ("tick", (True,)),
}


def to_mixer_array(x, fmt, channels):
    """Convertit un signal [-1, 1] au format reel du mixer (pygame.mixer.get_init())."""
    x = np.clip(x, -1.0, 1.0)
    bits = abs(fmt)
    if bits == 8:
        a = (x * 127.0).astype(np.int8) if fmt < 0 else (x * 127.0 + 128.0).astype(np.uint8)
    elif bits == 32:
        a = x.astype(np.float32)
    else:
        a = (x * 32767.0).astype(np.int16)
    if channels == 1:
        return np.ascontiguousarray(a)
    return np.ascontiguousarray(np.column_stack([a] * channels))


DRUM_SOUNDS = ("kick", "snare", "tom_hi", "tom_lo", "hihat", "crash")
LOUD_ACHIEVED = {}      # nom -> gain reellement obtenu par la variante "_loud"


def loudness(x, sr):
    # Niveau percu d'une percussion : RMS des 100 premieres ms
    head = x[: int(0.1 * sr)]
    return float(np.sqrt(np.mean(head ** 2)))


def make_loud(x, gain, sr, max_drive=8.0):
    """Variante plus forte (coup appuye) sans depasser 0 dB : saturation tanh minimale.

    Retourne (signal, gain obtenu). Le pic reste a 0.98 : c'est le niveau
    moyen qui monte, comme une peau frappee plus fort qui sonne plus dur.
    """
    peak = float(np.max(np.abs(x)))
    if peak <= 0:
        return x, 1.0
    if peak * gain <= 0.98:
        # Assez de marge (cymbales) : simple gain, timbre intact
        return x * gain, gain
    base = loudness(x, sr)

    def drive(d):
        y = np.tanh(d * x / peak)
        return y / np.max(np.abs(y)) * 0.98

    lo, hi = 1e-3, max_drive
    if loudness(drive(hi), sr) < gain * base:
        y = drive(hi)
        return y, loudness(y, sr) / base
    for _ in range(30):
        mid = (lo + hi) / 2
        if loudness(drive(mid), sr) < gain * base:
            lo = mid
        else:
            hi = mid
    y = drive(hi)
    return y, loudness(y, sr) / base


def build_sounds(pygame, mixer_init, loud_gain=1.5):
    """Retourne un dict nom -> pygame.mixer.Sound. mixer_init = pygame.mixer.get_init().

    Chaque son de batterie a aussi une variante "<nom>_loud", loud_gain fois
    plus forte (gain reellement obtenu dans LOUD_ACHIEVED).
    """
    sr, fmt, channels = mixer_init
    s = Synth(sr)
    out = {}
    for name, (method, args) in SOUND_NAMES.items():
        x = Synth.fade_out(getattr(s, method)(*args), sr)
        out[name] = pygame.sndarray.make_sound(to_mixer_array(x, fmt, channels))
        if name in DRUM_SOUNDS:
            loud, LOUD_ACHIEVED[name] = make_loud(x, loud_gain, sr)
            out[name + "_loud"] = pygame.sndarray.make_sound(to_mixer_array(loud, fmt, channels))
    return out
