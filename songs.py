# PiDrum - comptines du domaine public pour le mode chanson
#
# Chaque chanson = melodie en (note MIDI, duree en temps). La musique est
# synthetisee au demarrage (melodie + basse + shaker) et la partition des
# pads est deduite de la melodie selon le niveau.

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


def chart(song, level, pad_order):
    """Notes a frapper : liste de (temps, pad).

    Niveau 0 : une note tous les 2 temps ; 1 : sur chaque temps ; 2 : toutes.
    La hauteur de la note choisit le pad (grave -> premier de pad_order).
    """
    notes = []
    for b, midi, _ in onsets(song):
        on_beat = abs(b - round(b)) < 1e-6
        if level <= 0 and not (on_beat and int(round(b)) % 2 == 0):
            continue
        if level == 1 and not on_beat:
            continue
        notes.append((b, midi))
    if not pad_order:
        return []
    pitches = sorted({m for _, m in notes})
    n = len(pad_order)
    rank = {m: min(n - 1, i * n // max(1, len(pitches))) for i, m in enumerate(pitches)}
    return [(b, pad_order[rank[m]]) for b, m in notes]


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


_rendered = {}   # sr -> {id: signal}, evite de resynthetiser (tests)


def build_song_sounds(pygame, mixer_init):
    """Retourne un dict "song_<id>" -> pygame.mixer.Sound."""
    sr, fmt, channels = mixer_init
    if sr not in _rendered:
        synth = Synth(sr, seed=11)
        _rendered[sr] = {s["id"]: render(s, synth) for s in SONGS}
    return {"song_" + sid: pygame.sndarray.make_sound(to_mixer_array(x, fmt, channels))
            for sid, x in _rendered[sr].items()}
