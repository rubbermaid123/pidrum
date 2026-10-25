#!/usr/bin/env python3
# PiDrum - tests headless (pilotes SDL factices, temps synthetique)
#
#   python3 test_pidrum.py            -> tests + captures PNG dans shots/
#
# Aucun kit requis. Sur le Pi, arreter le service avant (il tient l'audio).

import os
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SHOTS = os.path.join(HERE, "shots")

import numpy as np  # noqa: E402
import pygame  # noqa: E402

import map_pads  # noqa: E402
import pidrum  # noqa: E402
import sounds  # noqa: E402

DT = 1 / 60


class Harness:
    def __init__(self):
        self.app = pidrum.App(window=True, use_kit=False)
        self.t = 1000.0
        self.app.reset_clock(self.t)

    def run(self, sec):
        for _ in range(int(round(sec * 60))):
            self.t += DT
            self.app.update(self.t, DT)
            self.app.draw(self.t)

    def hit(self, pad):
        self.app.handle("down", pad, self.t)
        self.app.handle("up", pad, self.t + 0.001)

    def hold_start(self, sec):
        self.app.handle("down", "start", self.t)
        self.run(sec)
        self.app.handle("up", "start", self.t)

    def goto(self, mode_cls):
        while not isinstance(self.app.mode, mode_cls):
            self.hold_start(pidrum.HOLD_TO_SWITCH + 0.1)

    def shot(self, name):
        os.makedirs(SHOTS, exist_ok=True)
        pygame.image.save(self.app.screen, os.path.join(SHOTS, name + ".png"))


class TestSounds(unittest.TestCase):
    def test_finite_and_bounded(self):
        s = sounds.Synth(44100)
        for name in sounds.SOUND_NAMES:
            x = getattr(s, sounds.SOUND_NAMES[name][0])(*sounds.SOUND_NAMES[name][1])
            self.assertTrue(np.all(np.isfinite(x)), name)
            self.assertLessEqual(float(np.max(np.abs(x))), 1.0, name)
            self.assertGreater(len(x), 0, name)

    def test_attack_is_immediate(self):
        # Le son d'un pad doit demarrer tout de suite : pas de silence en tete
        s = sounds.Synth(44100)
        for name in ("kick", "snare", "tom_hi", "tom_lo", "hihat", "crash"):
            x = getattr(s, name)()
            first = np.argmax(np.abs(x) > 0.05)
            self.assertLess(first, 44100 * 0.003, name)

    def test_sound_arrays_match_mixer(self):
        for ch in (1, 2):
            a = sounds.to_mixer_array(np.zeros(10), -16, ch)
            self.assertEqual(a.dtype, np.int16)
            self.assertEqual(a.shape, (10,) if ch == 1 else (10, ch))


class TestKeyDecoder(unittest.TestCase):
    KEY, SYN = 1, 0

    def frame(self, dec, *presses):
        out = []
        for code, value in presses:
            out += dec.feed(self.KEY, code, value)
        return out + dec.feed(self.SYN, 0, 0)

    def test_single_keys(self):
        dec = pidrum.KeyDecoder({"red": 305, "kick": 308})
        self.assertEqual(self.frame(dec, (305, 1)), [("down", "red")])
        self.assertEqual(self.frame(dec, (305, 0)), [("up", "red")])
        self.assertEqual(self.frame(dec, (999, 1)), [])

    def test_press_and_release_same_frame(self):
        dec = pidrum.KeyDecoder({"red": 305})
        self.assertEqual(self.frame(dec, (305, 1), (305, 0)), [("down", "red"), ("up", "red")])
        self.assertEqual(self.frame(dec, (305, 1)), [("down", "red")])

    def test_combo_wins_over_subset(self):
        # couleur + modificateur cymbale = cymbale ; couleur seule = pad
        dec = pidrum.KeyDecoder({"blue": 304, "yellow": [304, 311]})
        self.assertEqual(self.frame(dec, (304, 1), (311, 1)), [("down", "yellow")])
        self.assertEqual(self.frame(dec, (304, 0), (311, 0)), [("up", "yellow")])
        self.assertEqual(self.frame(dec, (304, 1)), [("down", "blue")])

    def test_two_pads_same_frame(self):
        dec = pidrum.KeyDecoder({"red": 305, "kick": 308})
        out = self.frame(dec, (305, 1), (308, 1))
        self.assertEqual(sorted(out), [("down", "kick"), ("down", "red")])


class FakeEvent:
    def __init__(self, ev_type, code, value):
        self.type, self.code, self.value = ev_type, code, value


class FakeDevice:
    """Faux InputDevice : un socket sert de fd pour select()."""

    def __init__(self, name):
        import socket
        self.name = name
        self.rx, self.tx = socket.socketpair()
        self.rx.setblocking(False)
        self.fd = self.rx.fileno()
        self.pending = []
        self.gone = False

    def push(self, *events):
        self.pending.extend(events)
        self.tx.send(b"x")

    def unplug(self):
        self.gone = True
        self.tx.send(b"x")

    def read(self):
        if self.gone:
            raise OSError(19, "No such device")
        try:
            self.rx.recv(4096)
        except BlockingIOError:
            raise
        out, self.pending = self.pending, []
        return iter(out)

    def close(self):
        self.rx.close()
        self.tx.close()


class TestEvdevThread(unittest.TestCase):
    def test_thread_reads_reconnects_and_calls_back(self):
        import threading
        import types
        devices, opened = [], []
        fake = types.ModuleType("evdev")
        fake.list_devices = lambda: ["/dev/input/event%d" % i for i in range(len(devices))]
        fake.InputDevice = lambda path: devices[int(path[-1])]
        sys.modules["evdev"] = fake
        old_every = pidrum.RECONNECT_EVERY
        pidrum.RECONNECT_EVERY = 0.05
        got = []
        cond = threading.Condition()

        def cb(kind, role, t):
            with cond:
                got.append((kind, role))
                cond.notify_all()

        def wait_for(n):
            with cond:
                self.assertTrue(cond.wait_for(lambda: len(got) >= n, timeout=3), got)

        try:
            inp = pidrum.EvdevInput({"device_name": "Kit", "keys": {"red": 305}}, cb)
            inp.start()
            d1 = FakeDevice("Kit")
            devices.append(d1)
            opened.append(d1)
            import time as _t
            deadline = _t.monotonic() + 3
            while inp.dev is None and _t.monotonic() < deadline:
                _t.sleep(0.01)
            d1.push(FakeEvent(1, 305, 1), FakeEvent(0, 0, 0))
            wait_for(1)
            d1.unplug()
            d2 = FakeDevice("Kit")
            devices[0] = d2
            opened.append(d2)
            while inp.dev is not d2 and _t.monotonic() < deadline + 3:
                _t.sleep(0.01)
            d2.push(FakeEvent(1, 305, 0), FakeEvent(0, 0, 0),
                    FakeEvent(1, 305, 1), FakeEvent(0, 0, 0))
            wait_for(2)
            self.assertEqual(got[:2], [("down", "red"), ("down", "red")])
            inp.stop()
        finally:
            pidrum.RECONNECT_EVERY = old_every
            del sys.modules["evdev"]
            for d in opened:
                try:
                    d.close()
                except OSError:
                    pass


class TestMapAnalyze(unittest.TestCase):
    def hits(self, *sets):
        return [(set(s), {}) for s in sets]

    def base(self):
        return {
            "red": self.hits({305}, {305}, {305}),
            "yellow": self.hits({307}, {307}, {307}),
            "blue": self.hits({304}, {304}, {304}),
            "orange": self.hits({311}, {311}, {311}),
            "green": self.hits({306}, {306}, {306}),
            "kick": self.hits({308}, {308}, {308}),
            "start": self.hits({315}, {315}, {315}),
            "select": self.hits({314}, {314}, {314}),
        }

    def test_unique_keys(self):
        keys, problems = map_pads.analyze(self.base())
        self.assertEqual(keys["red"], 305)
        self.assertEqual(keys["kick"], 308)
        self.assertEqual(problems, [])

    def test_extra_modifier_ignored_when_color_unique(self):
        cap = self.base()
        cap["red"] = self.hits({305, 400}, {305, 400}, {305})
        cap["blue"] = self.hits({304, 400}, {304, 400}, {304, 400})
        keys, _ = map_pads.analyze(cap)
        self.assertEqual(keys["red"], 305)
        self.assertEqual(keys["blue"], 304)

    def test_shared_color_becomes_combo(self):
        # cymbale jaune = bouton bleu + modificateur cymbale
        cap = self.base()
        cap["yellow"] = self.hits({304, 401}, {304, 401}, {304, 401})
        cap["orange"] = self.hits({306, 401}, {306, 401}, {306, 401})
        keys, problems = map_pads.analyze(cap)
        self.assertEqual(keys["yellow"], [304, 401])
        self.assertEqual(keys["orange"], [306, 401])
        self.assertEqual(keys["blue"], 304)
        dec = pidrum.KeyDecoder(keys)
        out = dec.feed(1, 304, 1) + dec.feed(1, 401, 1) + dec.feed(0, 0, 0)
        self.assertEqual(out, [("down", "yellow")])
        self.assertTrue(any("combinaison" in p for p in problems))

    def test_missing_role_reported(self):
        cap = self.base()
        cap["select"] = []
        keys, problems = map_pads.analyze(cap)
        self.assertNotIn("select", keys)
        self.assertTrue(any(p.startswith("select") for p in problems))


class TestApp(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        pygame.quit()

    def test_long_start_switches_mode(self):
        self.assertIsInstance(self.h.app.mode, pidrum.FreePlay)
        self.h.hold_start(pidrum.HOLD_TO_SWITCH + 0.1)
        self.assertIsInstance(self.h.app.mode, pidrum.CopyMe)

    def test_short_start_ignored(self):
        for _ in range(5):
            self.h.hold_start(0.3)
            self.h.run(0.2)
        self.assertIsInstance(self.h.app.mode, pidrum.FreePlay)

    def test_debounce(self):
        calls = []
        self.h.app.mode.on_hit = lambda app, pad, t: calls.append(pad)
        self.h.app.handle("down", "red", self.h.t)
        self.h.app.handle("down", "red", self.h.t + pidrum.DEBOUNCE / 2)
        self.h.app.handle("down", "red", self.h.t + pidrum.DEBOUNCE * 2)
        self.assertEqual(calls, ["red", "red"])

    def test_kit_event_plays_now_reacts_later_with_hit_time(self):
        played, hits = [], []
        self.h.app.play = lambda name, volume=1.0: played.append(name)
        self.h.app.mode.on_hit = lambda app, pad, t: hits.append((pad, t))
        t_hit = self.h.t + 0.004
        self.h.app.kit_event("down", "green", t_hit)
        self.assertEqual(played, ["tom_lo"])     # son immediat
        self.assertEqual(hits, [])               # logique au prochain tour de boucle
        self.h.app.kit_event("up", "green", t_hit + 0.05)
        self.h.app.process_kit()
        self.assertEqual(hits, [("green", t_hit)])

    def test_kit_start_hold_switches_mode(self):
        self.h.app.kit_event("down", "start", self.h.t)
        self.h.app.process_kit()
        self.h.run(pidrum.HOLD_TO_SWITCH + 0.1)
        self.assertIsInstance(self.h.app.mode, pidrum.CopyMe)

    def test_copyme_wrong_hit_does_not_advance(self):
        self.h.goto(pidrum.CopyMe)
        m = self.h.app.mode
        self.h.run(m.STEP * len(m.seq) + 1.5)
        self.assertEqual(m.state, "wait")
        wrong = next(p for p in pidrum.GAME_PADS if p != m.seq[m.idx])
        self.h.hit(wrong)
        self.assertEqual(m.idx, 0)
        self.assertEqual(m.state, "wait")

    def test_copyme_three_wins_lengthen(self):
        self.h.goto(pidrum.CopyMe)
        m = self.h.app.mode
        self.assertEqual(m.length, 1)
        for _ in range(3):
            while m.state != "wait":
                self.h.run(0.1)
            for pad in list(m.seq):
                self.h.hit(pad)
                self.h.run(0.1)
        self.assertEqual(m.length, 2)

    def test_copyme_never_uses_kick_by_default(self):
        self.h.goto(pidrum.CopyMe)
        m = self.h.app.mode
        for _ in range(50):
            m.new_round(self.h.t)
            self.assertNotIn("kick", m.seq)

    def _beat_hit_at(self, m, n, offset):
        target_t = m.t0 + n * m.period + pidrum.JUDGE_OFFSET + offset
        self.h.run(max(0.0, target_t - self.h.t))
        self.h.t = target_t
        self.h.hit(m.target(n))

    def test_beat_eight_hits_level_up(self):
        self.h.goto(pidrum.Beat)
        m = self.h.app.mode
        self.assertEqual(m.level, 0)
        offsets = [0.0, 0.1, -0.1, 0.2, -0.2, 0.05, -0.05, 0.0]
        for i, off in enumerate(offsets):
            self._beat_hit_at(m, i, off)
        self.assertEqual(m.level, 1)

    def test_beat_off_window_not_counted(self):
        self.h.goto(pidrum.Beat)
        m = self.h.app.mode
        # Pile entre deux temps : hors fenetre
        self._beat_hit_at(m, 2, m.period / 2 - 0.001)
        self.assertEqual(m.good, 0)
        # Bon temps mais mauvais pad : pas compte non plus
        target_t = m.t0 + 4 * m.period
        self.h.run(target_t - self.h.t)
        wrong = next(p for p in pidrum.GAME_PADS if p != m.target(4))
        self.h.hit(wrong)
        self.assertEqual(m.good, 0)

    def test_beat_double_hit_same_beat_counts_once(self):
        self.h.goto(pidrum.Beat)
        m = self.h.app.mode
        self._beat_hit_at(m, 1, 0.0)
        self.h.t += 0.1
        self.h.hit(m.target(1))
        self.assertEqual(m.good, 1)

    def test_particles_bounded(self):
        for _ in range(200):
            for pad in pidrum.GAME_PADS:
                self.h.hit(pad)
            self.h.run(DT)
        self.assertLessEqual(len(self.h.app.particles), pidrum.MAX_PARTICLES)


class TestShots(unittest.TestCase):
    def tearDown(self):
        pygame.quit()

    def test_screenshots(self):
        h = Harness()
        h.run(0.2)
        h.shot("1_free_idle")
        h.hit("red")
        h.hit("orange")
        h.hit("kick")
        h.run(0.08)
        h.shot("1_free_hits")
        h.app.handle("down", "start", h.t)
        h.run(0.9)
        h.shot("1_free_start_gauge")
        h.run(pidrum.HOLD_TO_SWITCH - 0.9 + 0.1)
        h.app.handle("up", "start", h.t)
        h.run(0.3)
        h.shot("2_copy_banner")
        m = h.app.mode
        h.run(m.STEP * len(m.seq) + pidrum.SIMON_HINT_DELAY + 0.5)
        h.shot("2_copy_wait_target")
        h.hit(m.seq[0])
        h.run(0.25)
        h.shot("2_copy_celebrate")
        h.goto(pidrum.Beat)
        b = h.app.mode
        h.run(b.t0 + 2.6 * b.period - h.t)
        h.shot("3_beat_approach")
        h.run(b.t0 + 3 * b.period - h.t)
        h.hit(b.target(3))
        h.run(0.1)
        h.shot("3_beat_reward")


if __name__ == "__main__":
    unittest.main(verbosity=2)
