#!/usr/bin/env python3
# PiDrum - mapping du kit de batterie
#
# Demande de frapper chaque pad 3 fois, detecte le bouton propre a chacun
# et ecrit pads.json a cote de ce script. A lancer par le parent, une seule fois.
#
# Si un element n'a aucun bouton a lui (kit qui envoie couleur + modificateur),
# on enregistre la combinaison de boutons commune a ses frappes, par exemple
# "yellow": [307, 311]. pidrum.py sait lire ces combinaisons.

import json
import os
import select
import sys
import time
from collections import Counter

ROLES = [
    ("red", "le pad ROUGE"),
    ("yellow", "la cymbale JAUNE"),
    ("blue", "le pad BLEU"),
    ("orange", "la cymbale ORANGE"),
    ("green", "le pad VERT"),
    ("kick", "la PEDALE"),
    ("start", "le bouton START"),
    ("select", "le bouton SELECT"),
]
HINTS = ("drum", "guitar hero", "redoctane", "harmonix", "activision")
HITS = 3
MERGE_WINDOW = 0.08   # trames a moins de 80 ms = un seul coup
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pads.json")


def code_name(code):
    try:
        from evdev import ecodes
    except ImportError:
        return str(code)
    for table in (ecodes.BTN, ecodes.KEY):
        n = table.get(code)
        if n:
            return n if isinstance(n, str) else n[0]
    return str(code)


def abs_name(code):
    try:
        from evdev import ecodes
    except ImportError:
        return str(code)
    n = ecodes.ABS.get(code, code)
    return n if isinstance(n, (str, int)) else n[0]


def pick_device():
    import evdev
    devs = []
    for p in evdev.list_devices():
        try:
            devs.append(evdev.InputDevice(p))
        except OSError:
            pass
    if not devs:
        print("Aucun peripherique d'entree lisible.")
        print("Dongle branche ? Utilisateur dans le groupe 'input' (commande: groups) ?")
        sys.exit(1)
    cands = [d for d in devs if any(h in d.name.lower() for h in HINTS)]
    if len(cands) == 1:
        print("Kit detecte : %s  %s" % (cands[0].path, cands[0].name))
        return cands[0]
    for i, d in enumerate(devs):
        print("[%d] %s  %s" % (i, d.path, d.name))
    return devs[int(input("Numero du kit de batterie : "))]


def drain(dev):
    while True:
        r, _, _ = select.select([dev.fd], [], [], 0.3)
        if not r:
            return
        try:
            for _ in dev.read():
                pass
        except BlockingIOError:
            return


def capture(dev, timeout=30.0):
    """Retourne une liste de coups ; chaque coup = (set de touches, dict axes)."""
    from evdev import ecodes
    hits = []
    keys, axes = set(), {}
    last_frame_t = -1.0
    deadline = time.monotonic() + timeout
    while len(hits) < HITS and time.monotonic() < deadline:
        r, _, _ = select.select([dev.fd], [], [], 0.2)
        if not r:
            continue
        try:
            events = list(dev.read())
        except BlockingIOError:
            continue
        for ev in events:
            if ev.type == ecodes.EV_KEY and ev.value == 1:
                keys.add(ev.code)
            elif ev.type == ecodes.EV_ABS:
                axes[ev.code] = ev.value
            elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_REPORT:
                if keys:
                    ts = ev.timestamp()
                    if hits and ts - last_frame_t < MERGE_WINDOW:
                        hits[-1][0].update(keys)
                        hits[-1][1].update(axes)
                    else:
                        hits.append((set(keys), dict(axes)))
                        print("   coup %d/%d" % (len(hits), HITS))
                    last_frame_t = ts
                keys, axes = set(), {}
    return hits


def analyze(captured):
    """captured : role -> liste de (set de touches, axes).

    Retourne (keys_out, problems) ; keys_out[role] = code (int) ou
    combinaison triee (liste de codes).
    """
    counts = {r: Counter(k for keys, _ in h for k in keys) for r, h in captured.items()}
    seen = {r: set(c) for r, c in counts.items()}
    common = {}
    for r, h in captured.items():
        sets = [set(keys) for keys, _ in h if keys]
        common[r] = frozenset(set.intersection(*sets)) if sets else frozenset()

    keys_out, problems = {}, []
    for role, _ in ROLES:
        c = counts.get(role)
        if not c:
            problems.append("%s : aucune touche recue" % role)
            continue
        others = set().union(*(seen[o] for o in seen if o != role))
        unique = [k for k in c if k not in others]
        if unique:
            keys_out[role] = sorted(unique, key=lambda k: (-c[k], k))[0]
            continue
        # Aucun bouton propre : combinaison toujours presente sur ses frappes
        combo = common[role]
        clash = [o for o in common if o != role and common[o] == combo]
        if len(combo) >= 2 and not clash:
            keys_out[role] = sorted(combo)
            problems.append("%s : combinaison %s (a verifier en jeu libre)"
                            % (role, "+".join(code_name(k) for k in sorted(combo))))
        else:
            best = c.most_common(1)[0][0]
            keys_out[role] = best
            problems.append("%s : aucune touche ni combinaison unique, %s partage avec %s"
                            % (role, code_name(best), ", ".join(clash) or "un autre element"))
    return keys_out, problems


def describe(code):
    if isinstance(code, list):
        return "+".join(code_name(k) for k in code)
    return code_name(code)


def main():
    dev = pick_device()
    print("\nPour chaque element, frappe-le %d fois (pause d'une seconde entre les coups).\n" % HITS)
    captured = {}
    for role, label in ROLES:
        drain(dev)
        print("-> Frappe %s" % label)
        hits = capture(dev)
        if len(hits) < HITS:
            print("   (delai depasse, %d coup(s) recu(s))" % len(hits))
        captured[role] = hits
        time.sleep(0.5)

    print("\nTouches vues par element :")
    for role, hits in captured.items():
        print("  %-7s %s" % (role, " | ".join(
            "+".join(code_name(k) for k in sorted(keys)) for keys, _ in hits) or "-"))

    keys_out, problems = analyze(captured)
    print("\nResultat :")
    for role, code in keys_out.items():
        print("  %-7s -> %-20s (%s)" % (role, describe(code), code))

    print("\nAxes analogiques bouges pendant les frappes (velocite possible, pour plus tard) :")
    for role, hits in captured.items():
        vals = {}
        for _, axes in hits:
            for code, v in axes.items():
                vals.setdefault(code, []).append(v)
        if vals:
            desc = ", ".join("%s=%s" % (abs_name(c), v) for c, v in sorted(vals.items()))
            print("  %-7s %s" % (role, desc))

    if problems:
        print("\nATTENTION :")
        for p in problems:
            print("  - " + p)

    cfg = {
        "device_name": dev.name,
        "keys": keys_out,
        "key_names": {r: describe(k) for r, k in keys_out.items()},
    }
    with open(OUT, "w") as f:
        json.dump(cfg, f, indent=2)
    print("\nEcrit : " + OUT)


if __name__ == "__main__":
    main()
