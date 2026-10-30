# PiDrum - comptines du domaine public pour le mode chanson
#
# Chaque chanson = melodie en (note MIDI, duree en temps). La musique est
# synthetisee au demarrage (melodie + basse + shaker) et la partition des
# pads est deduite de la melodie selon le niveau.
#
# Accompagnements du jeu libre (GROOVES) : boucles basse + accords + shaker,
# sans batterie (c'est l'enfant qui la joue), une par niveau.

import numpy as np

from sounds import Synth, to_mixer_array

COUNT_IN = 4        # temps de decompte (ticks) avant la melodie
TAIL = 2            # temps de silence musical apres la derniere note


def _frere_jacques():
    a = [(60, 1), (62, 1), (64, 1), (60, 1)]
    b = [(64, 1), (65, 1), (67, 2)]
    c = [(67, .5), (69, .5), (67, .5), (65, .5), (64, 1), (60, 1)]
    d = [(60, 1), (55, 1), (60, 2)]
    return a * 2 + b * 2 + c * 2 + d * 2


def _au_clair_de_la_lune():
    a = [(60, 1), (60, 1), (60, 1), (62, 1), (64, 2), (62, 2),
         (60, 1), (64, 1), (62, 1), (62, 1), (60, 4)]
    b = [(62, 1), (62, 1), (62, 1), (62, 1), (57, 2), (57, 2),
         (62, 1), (60, 1), (59, 1), (57, 1), (55, 4)]
    return a + a + b + a


def _ah_vous_dirai_je_maman():
    a = [(60, 1), (60, 1), (67, 1), (67, 1), (69, 1), (69, 1), (67, 2)]
    b = [(65, 1), (65, 1), (64, 1), (64, 1), (62, 1), (62, 1), (60, 2)]
    c = [(67, 1), (67, 1), (65, 1), (65, 1), (64, 1), (64, 1), (62, 2)]
    return a + b + c + c + a + b


SONGS = [
    {"id": "frere_jacques", "bpm": 88, "melody": _frere_jacques()},
    {"id": "au_clair", "bpm": 92, "melody": _au_clair_de_la_lune()},
    {"id": "ah_vous_dirai", "bpm": 92, "melody": _ah_vous_dirai_je_maman()},
]


def onsets(song):
    """Liste de (temps, note MIDI, duree) a partir du debut de la melodie."""
    out, b = [], 0.0
    for midi, dur in song["melody"]:
        out.append((b, midi, dur))
        b += dur
    return out


def length_beats(song):
    return sum(d for _, d in song["melody"])


def chart(song, density, pad_order):
    """Notes a frapper : liste de (temps, pad), triee par temps.

    Densite 0 : une note par mesure (4 temps) ; 1 : tous les 2 temps ;
    2 : sur chaque temps ; 3 : toutes les notes ; 4 : toutes + cymbale
    (orange, sinon jaune) en accord sur chaque debut de mesure.
    La hauteur de la note choisit le pad (grave -> premier de pad_order).
    """
    every = {0: 4, 1: 2, 2: 1}.get(density)
    notes = []
    for b, midi, _ in onsets(song):
        on_beat = abs(b - round(b)) < 1e-6
        if every is not None and not (on_beat and int(round(b)) % every == 0):
            continue
        notes.append((b, midi))
    if not pad_order:
        return []
    pitches = sorted({m for _, m in notes})
    n = len(pad_order)
    rank = {m: min(n - 1, i * n // max(1, len(pitches))) for i, m in enumerate(pitches)}
    out = [(b, pad_order[rank[m]]) for b, m in notes]
    if density >= 4:
        for b, pad in list(out):
            if abs(b % 4) < 1e-6:
                accent = next((p for p in ("orange", "yellow") if p in pad_order and p != pad), None)
                if accent:
                    out.append((b, accent))
    return sorted(out, key=lambda x: x[0])


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


def _bar_root(pitches):
    # Accord le plus probable de la mesure : do (C), fa (F) ou sol (G)
    chords = {48: {0, 4, 7}, 53: {5, 9, 0}, 43: {7, 11, 2}}
    pcs = [p % 12 for p in pitches]
    return max(chords, key=lambda r: (sum(pc in chords[r] for pc in pcs), r == 48))


def render(song, synth):
    """Audio mono [-1, 1] : decompte, melodie, basse, shaker."""
    sr = synth.sr
    period = 60.0 / song["bpm"]
    total = COUNT_IN + length_beats(song) + TAIL
    out = np.zeros(int(total * period * sr) + sr // 10)

    def add(x, beat, gain):
        i = int(beat * period * sr)
        x = x[: len(out) - i]
        out[i:i + len(x)] += x * gain

    for k in range(COUNT_IN):
        add(synth.tick(k == 0), k, 1.0)

    for b, midi, dur in onsets(song):
        d = min(dur * period, 1.2)
        f = midi_hz(midi)
        voice = synth.tone(f, d) + 0.45 * synth.tone(2 * f, d) * synth.env(d, 0.15)
        voice *= synth.env(d, max(0.12, 0.6 * d), attack=0.006)
        add(voice, COUNT_IN + b, 0.30)

    beats = int(length_beats(song))
    on = onsets(song)
    for bar in range(0, beats, 4):
        pitches = [m for b, m, _ in on if bar <= b < bar + 4]
        root = _bar_root(pitches) if pitches else 48
        for k in (0, 2):
            if bar + k >= beats:
                break
            d = period * 1.5
            f = midi_hz(root)
            bass = synth.tone(f, d) + 0.25 * synth.tone(3 * f, d)
            add(bass * synth.env(d, 0.35, attack=0.008), COUNT_IN + bar + k, 0.22)
    for k in range(beats):
        d = 0.05
        add(Synth.hp(synth.noise(d), 2) * synth.env(d, 0.012), COUNT_IN + k, 0.05)

    out = Synth.fade_out(out, sr, 30.0)
    return Synth.norm(out, 0.6)


# Accompagnements du jeu libre : 4 mesures en boucle, du plus calme au plus dense
GROOVES = {
    "pulse": {"bpm": 72, "roots": [48, 53, 55, 48], "style": "pulse"},
    "walk": {"bpm": 80, "roots": [48, 53, 55, 48], "style": "walk"},
    "pop": {"bpm": 90, "roots": [48, 55, 57, 53], "style": "pop"},
    "rock": {"bpm": 100, "roots": [45, 53, 48, 55], "style": "rock"},
    "funk": {"bpm": 110, "roots": [50, 50, 55, 53], "style": "funk"},
}
MINOR_ROOTS = {45, 50, 57}      # la, re : accords mineurs


def groove_beats(g):
    return 4 * len(g["roots"])


def render_groove(g, synth):
    """Audio mono [-1, 1] bouclable : la queue des notes revient au debut."""
    sr = synth.sr
    period = 60.0 / g["bpm"]
    n = int(round(groove_beats(g) * period * sr))
    out = np.zeros(n + 2 * sr)

    def add(x, beat, gain):
        i = int(beat * period * sr)
        out[i:i + len(x)] += x * gain

    def bass(midi, beats, gain=0.30):
        d = beats * period
        f = midi_hz(midi)
        x = synth.tone(f, d) + 0.25 * synth.tone(2 * f, d) + 0.1 * synth.tone(3 * f, d)
        return x * synth.env(d, max(0.08, 0.45 * d), attack=0.006) * gain

    def chord(root, beats, bright=0.3, decay=0.5):
        d = beats * period
        third = 3 if root in MINOR_ROOTS else 4
        x = np.zeros(int(sr * d))
        for k in (0, third, 7):
            f = midi_hz(root + 12 + k)
            x += synth.tone(f, d) + bright * synth.tone(2 * f, d)
        return x * synth.env(d, decay * d, attack=0.01) / 3.0

    def power(root, beats):
        # Accord "guitare" : fondamentale + quinte, harmoniques en dents de scie
        d = beats * period
        x = np.zeros(int(sr * d))
        for k in (0, 7, 12):
            f = midi_hz(root + 12 + k)
            for h in range(1, 6):
                x += synth.tone(f * h, d) / h
        return x * synth.env(d, 0.8 * d, attack=0.015) / 6.0

    shaker = Synth.hp(synth.noise(0.05), 2) * synth.env(0.05, 0.012)
    thump = synth.sweep(90, 50, 0.3, 0.03) * synth.env(0.3, 0.09)
    style = g["style"]
    for bar, root in enumerate(g["roots"]):
        b0 = bar * 4
        if style == "pulse":
            for k in (0, 2):
                add(bass(root, 2), b0 + k, 1.0)
                add(thump, b0 + k, 0.35)
            for k in range(4):
                add(shaker, b0 + k, 0.05)
        elif style == "walk":
            for k, iv in enumerate((0, 7, 12, 7)):
                add(bass(root + iv, 1), b0 + k, 1.0)
                add(shaker, b0 + k, 0.06)
            add(chord(root, 4, 0.2, 0.6), b0, 0.18)
        elif style == "pop":
            for k in range(8):
                add(bass(root, 0.5, 0.26), b0 + k * 0.5, 1.0)
                add(shaker, b0 + k * 0.5, 0.06 if k % 2 else 0.08)
            for k in (1, 3):
                add(chord(root, 1, 0.35, 0.35), b0 + k, 0.28)
        elif style == "rock":
            for k in range(8):
                add(bass(root, 0.5, 0.28), b0 + k * 0.5, 1.0)
                add(shaker, b0 + k * 0.5, 0.07 if k % 2 else 0.09)
            add(power(root, 4), b0, 0.30)
        else:
            for pos, iv in ((0, 0), (0.75, 0), (1.5, 12), (2, 0), (2.75, 7), (3.5, 12)):
                add(bass(root + iv, 0.4, 0.3), b0 + pos, 1.0)
            for pos in (0.5, 1.5, 2.5, 3.5):
                add(chord(root, 0.3, 0.5, 0.25), b0 + pos, 0.30)
            for k in range(16):
                add(shaker, b0 + k * 0.25, 0.05 if k % 2 else 0.08)
    # Bouclage : ce qui depasse la fin rejoue sur le debut
    tail = out[n:]
    out = out[:n].copy()
    out[:len(tail)] += tail[:n]
    return Synth.norm(out, 0.6)


_rendered = {}   # sr -> {nom: signal}, evite de resynthetiser (tests)


def build_song_sounds(pygame, mixer_init):
    """Retourne un dict "song_<id>" / "groove_<id>" -> pygame.mixer.Sound."""
    sr, fmt, channels = mixer_init
    if sr not in _rendered:
        synth = Synth(sr, seed=11)
        r = {"song_" + s["id"]: render(s, synth) for s in SONGS}
        r.update({"groove_" + gid: render_groove(g, synth) for gid, g in GROOVES.items()})
        _rendered[sr] = r
    return {name: pygame.sndarray.make_sound(to_mixer_array(x, fmt, channels))
            for name, x in _rendered[sr].items()}
