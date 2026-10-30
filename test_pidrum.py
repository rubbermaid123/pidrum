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
import songs  # noqa: E402
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

    def test_snare_distinct_from_toms_and_hihat(self):
        # Part d'energie : corps (150-400 Hz) et aigus (> 2 kHz)
        def bands(x, sr=44100):
            p = np.abs(np.fft.rfft(x)) ** 2
            f = np.fft.rfftfreq(len(x), 1 / sr)
            return p[f < 400].sum() / p.sum(), p[f > 2000].sum() / p.sum()
        s = sounds.Synth(44100)
        low, hi = bands(s.snare())
        self.assertGreater(low, 0.6)             # une peau ronde, pas une cymbale
        self.assertGreater(hi, 0.08)             # des fils, pas un tom
        rms = lambda x: float(np.sqrt(np.mean(x[:2205] ** 2)))   # 50 premieres ms
        self.assertGreater(rms(s.snare()), 0.7 * rms(s.tom_hi()))   # pas plus faible
        self.assertLess(bands(s.tom_hi())[1], 0.05)
        self.assertGreater(bands(s.hihat())[1], 0.9)

    def test_sound_arrays_match_mixer(self):
        for ch in (1, 2):
            a = sounds.to_mixer_array(np.zeros(10), -16, ch)
            self.assertEqual(a.dtype, np.int16)
            self.assertEqual(a.shape, (10,) if ch == 1 else (10, ch))


class TestSongs(unittest.TestCase):
    PADS4 = ["red", "blue", "green", "orange"]

    def test_render_finite_bounded_with_count_in(self):
        s = sounds.Synth(48000)
        for song in songs.SONGS:
            x = songs.render(song, s)
            self.assertTrue(np.all(np.isfinite(x)), song["id"])
            self.assertLessEqual(float(np.max(np.abs(x))), 1.0, song["id"])
            period = 60.0 / song["bpm"]
            beats = songs.COUNT_IN + songs.length_beats(song) + songs.TAIL
            self.assertAlmostEqual(len(x) / 48000, beats * period, delta=0.2)

    def test_chart_densities_get_denser(self):
        for song in songs.SONGS:
            counts = [len(songs.chart(song, d, self.PADS4)) for d in range(5)]
            self.assertGreater(counts[0], 0, song["id"])
            for a, b in zip(counts, counts[1:]):
                self.assertLessEqual(a, b, song["id"])
            # densite 0 : une note par mesure au plus
            beats = [b for b, _ in songs.chart(song, 0, self.PADS4)]
            self.assertTrue(all(b2 - b1 >= 4 for b1, b2 in zip(beats, beats[1:])))

    def test_expert_chart_has_cymbal_chords(self):
        for song in songs.SONGS:
            notes = songs.chart(song, 4, self.PADS4)
            self.assertEqual(notes, sorted(notes, key=lambda n: n[0]))
            by_beat = {}
            for b, pad in notes:
                by_beat.setdefault(b, []).append(pad)
            chords = [p for p in by_beat.values() if len(p) > 1]
            self.assertTrue(chords, song["id"])
            self.assertTrue(all(len(set(p)) == len(p) and "orange" in p for p in chords))

    def test_chart_only_uses_given_pads(self):
        for song in songs.SONGS:
            for d in range(5):
                used = {p for _, p in songs.chart(song, d, ["red", "blue"])}
                self.assertTrue(used <= {"red", "blue"})
            self.assertEqual(songs.chart(song, 0, []), [])

    def test_count_in_longer_than_lead(self):
        # Une note doit pouvoir faire tout son trajet apres le depart de la musique
        lead = max(c["lead"] for c in pidrum.SONG_LEVELS)
        for song in songs.SONGS:
            self.assertGreaterEqual(songs.COUNT_IN * 60.0 / song["bpm"], lead)

    def test_grooves_loop_exactly_and_bounded(self):
        s = sounds.Synth(48000)
        for gid, g in songs.GROOVES.items():
            x = songs.render_groove(g, s)
            self.assertTrue(np.all(np.isfinite(x)), gid)
            self.assertLessEqual(float(np.max(np.abs(x))), 1.0, gid)
            period = 60.0 / g["bpm"]
            self.assertAlmostEqual(len(x) / 48000, songs.groove_beats(g) * period, delta=0.001)
        # Chaque niveau du jeu libre a son accompagnement (ou le silence)
        for groove in pidrum.FREE_LEVELS:
            self.assertTrue(groove is None or groove in songs.GROOVES)


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

        def cb(kind, role, t, vel=None):
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


class TestAudioPick(unittest.TestCase):
    def fake_asound(self, cards):
        # cards : liste de (id, sad_count ou None si pas d'ELD)
        import tempfile
        root = tempfile.mkdtemp()
        # Comme le vrai /proc/asound : un fichier "cards" et des liens par nom
        with open(os.path.join(root, "cards"), "w") as f:
            f.write("liste\n")
        for i, (card_id, sads) in enumerate(cards):
            d = os.path.join(root, "card%d" % i)
            os.makedirs(d)
            with open(os.path.join(d, "id"), "w") as f:
                f.write(card_id + "\n")
            if sads is not None:
                with open(os.path.join(d, "eld#0"), "w") as f:
                    f.write("monitor_name\t\tHP E273q\nsad_count\t\t%d\n" % sads)
        return root

    def test_monitor_without_speakers_uses_jack(self):
        root = self.fake_asound([("vc4hdmi0", 0), ("vc4hdmi1", 0), ("Headphones", None)])
        self.assertEqual(pidrum.pick_audio_device("auto", root), "plughw:CARD=Headphones,DEV=0")

    def test_tv_with_audio_uses_its_hdmi(self):
        root = self.fake_asound([("vc4hdmi0", 0), ("vc4hdmi1", 3), ("Headphones", None)])
        self.assertEqual(pidrum.pick_audio_device("auto", root), "plughw:CARD=vc4hdmi1,DEV=0")
        self.assertEqual(pidrum.pick_audio_device("jack", root), "plughw:CARD=Headphones,DEV=0")

    def test_no_asound_keeps_default(self):
        self.assertIsNone(pidrum.pick_audio_device("auto", os.path.join(HERE, "absent")))


class TestVelocity(unittest.TestCase):
    # Rapports reels du kit (GH World Tour PS3), releves le 2026-10-25
    IDLE = bytes.fromhex("00000f808000000000000000000000000000000000000000000002")

    def report(self, **bytes_at):
        r = bytearray(self.IDLE)
        for i, v in bytes_at.items():
            r[int(i[1:])] = v
        return bytes(r)

    def test_rising_edge_value_taken_once(self):
        dec = pidrum.VelocityDecoder({"red": 12, "orange": 16})
        dec.feed(self.report(b12=117), now=10.0)
        dec.feed(self.IDLE, now=10.04)
        self.assertEqual(dec.take("red", now=10.001), 117)
        self.assertIsNone(dec.take("red", now=10.002))      # deja consommee
        self.assertIsNone(dec.take("orange", now=10.002))

    def test_crosstalk_does_not_inflate_next_hit(self):
        # Coup fort sur le bleu : le vert affiche 33 sans etre frappe
        dec = pidrum.VelocityDecoder({"blue": 14, "green": 13})
        dec.feed(self.report(b14=81), now=1.0)
        dec.feed(self.report(b14=81, b13=33), now=1.01)
        dec.feed(self.IDLE, now=1.05)
        self.assertEqual(dec.take("blue", now=1.0), 81)
        # Le vrai coup vert, doux, arrive 2 s plus tard
        dec.feed(self.report(b13=28), now=3.0)
        self.assertEqual(dec.take("green", now=3.0), 28)

    def test_stale_value_ignored(self):
        dec = pidrum.VelocityDecoder({"red": 12})
        dec.feed(self.report(b12=90), now=5.0)
        self.assertIsNone(dec.take("red", now=5.0 + pidrum.VEL_FRESH + 0.05))

    def test_gain_curve(self):
        self.assertEqual(pidrum.velocity_gain(None), 1.0)
        self.assertAlmostEqual(pidrum.velocity_gain(0), pidrum.VEL_SOFT_GAIN)
        self.assertAlmostEqual(pidrum.velocity_gain(250), pidrum.VEL_HARD_GAIN)
        # Coups reels du kit : doux 28-40, forts 80-122
        for soft in (28, 31, 36, 40):
            self.assertLessEqual(pidrum.velocity_gain(soft), 0.95)
        for hard in (99, 117, 122):
            self.assertGreaterEqual(pidrum.velocity_gain(hard), 1.4)

    def test_loud_variants_reach_gain_without_clipping(self):
        s = sounds.Synth(44100)
        for name in sounds.DRUM_SOUNDS:
            x = getattr(s, name)()
            y, got = sounds.make_loud(x, 1.5, 44100)
            self.assertAlmostEqual(got, 1.5, delta=0.02, msg=name)
            self.assertLessEqual(float(np.max(np.abs(y))), 0.99, name)
            ratio = sounds.loudness(y, 44100) / sounds.loudness(x, 44100)
            self.assertAlmostEqual(ratio, got, places=3)

    def test_strike_uses_velocity_gain(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        app = pidrum.App(window=True, use_kit=False)
        try:
            calls = []
            app.play = lambda name, volume=1.0: calls.append((name, round(volume, 2)))
            app.kit_event("down", "red", 100.0, 25)      # tres doux : 90 % du son normal
            app.kit_event("down", "blue", 100.0, 125)    # tres fort : variante forte a fond
            app.kit_event("down", "green", 100.0, None)  # force inconnue : son normal
            self.assertEqual(calls[0], ("snare", 0.9))
            self.assertEqual(calls[1], ("tom_hi_loud", 1.0))
            self.assertEqual(calls[2], ("tom_lo", 1.0))
        finally:
            pygame.quit()


class FakeHid(FakeDevice):
    """Faux /dev/hidrawN : rapports bruts."""
    path = "/dev/hidraw-faux"

    def read_reports(self):
        try:
            self.rx.recv(4096)
        except BlockingIOError:
            return []
        out, self.pending = self.pending, []
        return out


class TestEvdevVelocityThread(unittest.TestCase):
    def test_thread_attaches_raw_velocity_to_hit(self):
        import threading
        import types
        dev, hid = FakeDevice("Kit"), FakeHid("Kit")
        fake = types.ModuleType("evdev")
        fake.list_devices = lambda: ["/dev/input/event0"]
        fake.InputDevice = lambda path: dev
        sys.modules["evdev"] = fake
        got, cond = [], threading.Condition()

        def cb(kind, role, t, vel=None):
            with cond:
                got.append((kind, role, vel))
                cond.notify_all()
        try:
            cfg = {"device_name": "Kit", "keys": {"red": 306},
                   "velocity": {"report_bytes": {"red": 12}}}
            inp = pidrum.EvdevInput(cfg, cb)
            inp._open_hid = lambda: hid
            inp.start()
            import time as _t
            deadline = _t.monotonic() + 3
            while inp.hid is None and _t.monotonic() < deadline:
                _t.sleep(0.01)
            # Comme le noyau : rapport brut d'abord, puis evenements evdev
            report = bytearray(TestVelocity.IDLE)
            report[0], report[12] = 0x04, 0x75      # bouton rouge, force 117
            hid.push(bytes(report))
            dev.push(FakeEvent(1, 306, 1), FakeEvent(0, 0, 0))
            with cond:
                self.assertTrue(cond.wait_for(lambda: got, timeout=3))
            self.assertEqual(got[0], ("down", "red", 0x75))
            inp.stop()
        finally:
            del sys.modules["evdev"]
            dev.close()
            hid.close()


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


class TestMapButtons(unittest.TestCase):
    KEYS = {"red": 306, "blue": 304, "green": 305, "yellow": 307, "start": 312}

    def test_shared_codes_not_written(self):
        cap = {"triangle": [({307}, {})], "circle": [({306}, {})], "cross": [], "square": [({304}, {})]}
        buttons, notes = map_pads.analyze_buttons(cap, self.KEYS)
        self.assertEqual(buttons, {})
        self.assertEqual(len(notes), 4)

    def test_own_code_written_and_used(self):
        cap = {"triangle": [({544}, {}), ({544}, {})], "circle": [({306}, {})]}
        buttons, _ = map_pads.analyze_buttons(cap, self.KEYS)
        self.assertEqual(buttons, {"triangle": 544})
        cfg = {"keys": self.KEYS, "buttons": buttons}
        self.assertEqual(pidrum.kit_keys(cfg)["triangle"], 544)
        shared = pidrum.shared_buttons(cfg)
        self.assertNotIn("yellow", shared)               # triangle a son propre code
        self.assertEqual(shared["red"], "circle")

    def test_button_with_pad_code_stays_shared(self):
        cfg = {"keys": self.KEYS, "buttons": {"circle": 306}}
        self.assertNotIn("circle", pidrum.kit_keys(cfg))
        self.assertEqual(pidrum.shared_buttons(cfg)["red"], "circle")


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

    def hold(self, *names, sec):
        for n in names:
            self.h.app.handle("down", n, self.h.t)
        self.h.run(sec)
        for n in names:
            self.h.app.handle("up", n, self.h.t)

    def test_select_start_hold_powers_off(self):
        old = pidrum.SHUTDOWN_CMD
        pidrum.SHUTDOWN_CMD = [sys.executable, "-c", "pass"]
        try:
            self.hold("select", "start", sec=pidrum.SHUTDOWN_HOLD + 0.1)
            app = self.h.app
            self.assertIsNotNone(app.shutdown_at)
            self.assertIsInstance(app.mode, pidrum.FreePlay)   # pas de changement de mode
            self.h.run(pidrum.SHUTDOWN_FADE + 0.1)
            self.assertTrue(app.shutdown_sent)
            self.assertIsNotNone(app.shutdown_at)              # reste en fondu noir
        finally:
            pidrum.SHUTDOWN_CMD = old

    def test_refused_poweroff_resumes_game(self):
        old = pidrum.SHUTDOWN_CMD
        pidrum.SHUTDOWN_CMD = [sys.executable, "-c", "import sys; sys.exit(1)"]
        try:
            self.hold("select", "start", sec=pidrum.SHUTDOWN_HOLD + 0.1)
            self.h.run(pidrum.SHUTDOWN_FADE + 0.1)
            self.assertIsNone(self.h.app.shutdown_at)
            calls = []
            self.h.app.mode.on_hit = lambda app, pad, t: calls.append(pad)
            self.h.hit("red")
            self.assertEqual(calls, ["red"])
        finally:
            pidrum.SHUTDOWN_CMD = old

    def test_short_combo_or_select_alone_does_nothing(self):
        self.hold("select", "start", sec=pidrum.SHUTDOWN_HOLD - 0.5)
        self.hold("select", sec=pidrum.SHUTDOWN_HOLD + 0.5)
        self.assertIsNone(self.h.app.shutdown_at)
        self.assertIsInstance(self.h.app.mode, pidrum.FreePlay)

    def start_song_mode(self):
        self.h.goto(pidrum.Song)
        m = self.h.app.mode
        while m.state != "play":
            self.h.run(0.1)
        return m

    def play_notes(self, m, offset=0.0, pad_of=None, upto=None, start=0):
        for n in list(m.notes)[start:upto]:
            target = n["t"] + pidrum.JUDGE_OFFSET + offset
            if target > self.h.t:
                self.h.run(target - self.h.t)
            self.h.t = max(self.h.t, target)
            self.h.hit(pad_of(n) if pad_of else n["pad"])

    def test_song_all_notes_hit_levels_up_and_next_song(self):
        m = self.start_song_mode()
        m.home = 1                     # le parent a choisi un niveau au-dessus
        first = m.song["id"]
        self.assertIsNotNone(m.channel)
        self.play_notes(m, offset=0.1)
        self.assertEqual(m.hits, len(m.notes))
        self.h.run(m.end_t - self.h.t + 0.1)
        self.assertEqual(m.state, "pause")
        self.assertEqual(m.level, 1)
        self.h.run(pidrum.SONG_PAUSE + 0.1)
        self.assertNotEqual(m.song["id"], first)

    def test_song_wrong_pad_or_off_time_not_counted_and_song_continues(self):
        m = self.start_song_mode()
        other = {"red": "blue", "blue": "red", "green": "red", "orange": "red"}
        self.play_notes(m, pad_of=lambda n: other[n["pad"]], upto=3)
        self.play_notes(m, offset=m.window + 0.05, start=3, upto=6)
        self.assertEqual(m.hits, 0)
        self.h.run(m.end_t - self.h.t + 0.1)
        self.assertEqual(m.state, "pause")       # toujours au bout
        self.assertEqual(m.level, 0)             # ne descend pas sous 0
        self.assertTrue(all(n["state"] == "miss" for n in m.notes))

    def test_song_never_climbs_above_chosen_level(self):
        m = self.start_song_mode()
        self.play_notes(m, offset=0.05)
        self.h.run(m.end_t - self.h.t + 0.1)
        self.assertEqual(m.level, 0)             # home = 0 : reste au niveau choisi

    def test_song_levels_change_chart(self):
        self.h.goto(pidrum.Song)
        m = self.h.app.mode
        counts = []
        for lv in range(len(pidrum.LEVELS)):
            m.home = m.level = lv
            m.start_song(self.h.t)
            counts.append(len(m.chart))
            if pidrum.SONG_LEVELS[lv]["pads"] == "drums":
                self.assertTrue({p for _, p in m.chart} <= set(pidrum.DRUM_PADS))
        self.assertEqual(counts, sorted(counts))
        self.assertLess(counts[0], counts[-1])

    def test_song_leaving_mode_stops_music(self):
        m = self.start_song_mode()
        stopped = []

        class FakeChannel:
            def stop(self):
                stopped.append(True)
        m.channel = FakeChannel()
        self.h.hold_start(pidrum.HOLD_TO_SWITCH + 0.1)
        self.assertIsInstance(self.h.app.mode, pidrum.FreePlay)
        self.assertEqual(stopped, [True])

    def test_song_respects_disabled_pads(self):
        self.h.app.disabled = {"yellow", "kick", "orange"}
        m = self.start_song_mode()
        self.assertTrue({n["pad"] for n in m.notes} <= {"red", "blue", "green"})

    def test_disabled_pads_never_requested(self):
        app = self.h.app
        app.disabled = {"yellow", "kick"}
        self.assertNotIn("yellow", app.target_pads())
        self.h.goto(pidrum.CopyMe)
        for _ in range(50):
            app.mode.new_round(self.h.t)
            self.assertNotIn("yellow", app.mode.seq)
        self.h.goto(pidrum.Beat)
        for lv in range(len(pidrum.LEVELS)):
            app.mode.level = lv
            app.mode.setup(self.h.t)
            for p in app.mode.patterns:
                self.assertNotIn("yellow", p)
                self.assertNotIn("kick", p)
        app.mode.level = 0
        app.mode.setup(self.h.t)
        self.assertIn(["red"], app.mode.patterns)
        played = []
        app.play = lambda name, volume=1.0: played.append(name)
        self.h.hit("yellow")
        self.assertEqual(played, [])

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

    def copy_mode(self, level):
        self.h.goto(pidrum.CopyMe)
        m = self.h.app.mode
        m.home = m.level = level
        m.setup(self.h.t)
        return m

    def copy_win(self, m):
        while m.state != "wait":
            self.h.run(0.1)
        for pad in list(m.seq):
            self.h.hit(pad)
            self.h.run(0.1)

    def test_copyme_wrong_hit_does_not_advance(self):
        self.h.goto(pidrum.CopyMe)
        m = self.h.app.mode
        self.h.run(pidrum.COPY_START_DELAY + pidrum.COPY_LEAD + m.step * len(m.seq) + 0.5)
        self.assertEqual(m.state, "wait")
        wrong = next(p for p in pidrum.GAME_PADS if p != m.seq[m.idx])
        self.h.hit(wrong)
        self.assertEqual(m.idx, 0)
        self.assertEqual(m.state, "wait")

    def test_copyme_three_wins_lengthen(self):
        m = self.copy_mode(1)
        self.assertEqual(m.length, 1)
        for _ in range(3):
            self.copy_win(m)
        self.assertEqual(m.length, 2)

    def test_copyme_apprenti_drums_only_and_climbs_to_chosen_level(self):
        m = self.copy_mode(0)
        m.home = 1
        for _ in range(30):
            m.new_round(self.h.t)
            self.assertTrue(set(m.seq) <= set(pidrum.DRUM_PADS))
        for _ in range(pidrum.COPY_WINS_TO_GROW):
            self.copy_win(m)
        self.assertEqual(m.level, 1)
        for _ in range(pidrum.COPY_WINS_TO_GROW * 2):
            self.copy_win(m)
        self.assertEqual(m.level, 1)             # jamais au-dessus du niveau choisi

    def copy_fail(self, m):
        while m.state != "wait":
            self.h.run(0.1)
        wrong = next(p for p in pidrum.GAME_PADS if p != m.seq[m.idx])
        for _ in range(pidrum.COPY_ERRORS_TO_SHRINK):
            self.h.hit(wrong)
            self.h.run(0.1)

    def test_copyme_errors_shrink_then_drop_level(self):
        m = self.copy_mode(3)
        m.length = 3
        m.new_round(self.h.t)
        self.copy_fail(m)
        self.assertEqual((m.level, m.length), (3, 2))   # raccourcit d'abord
        self.copy_fail(m)
        self.assertEqual(m.level, 2)                     # puis redescend d'un niveau
        self.assertEqual(m.home, 3)

    def test_copyme_expert_sequences(self):
        m = self.copy_mode(5)
        self.assertEqual(len(m.seq), pidrum.COPY_LEVELS[5]["min"])
        self.assertLess(m.step, pidrum.COPY_LEVELS[0]["step"])

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

    def test_beat_goal_hits_next_pattern(self):
        self.h.goto(pidrum.Beat)
        m = self.h.app.mode
        self.assertEqual((m.level, m.pattern_idx), (0, 0))
        offsets = [0.0, 0.1, -0.1, 0.2, -0.2, 0.05, -0.05, 0.25]
        for i in range(m.goal):
            self._beat_hit_at(m, i, offsets[i % len(offsets)])
        self.assertEqual(m.pattern_idx, 1)
        self.assertEqual(m.good, 0)

    def test_beat_all_patterns_climb_to_chosen_level(self):
        self.h.goto(pidrum.Beat)
        m = self.h.app.mode
        m.home = 1
        for _ in range(len(m.patterns)):
            for i in range(m.goal):
                self._beat_hit_at(m, i, 0.0)
        self.assertEqual(m.level, 1)
        self.assertEqual(m.period, 60.0 / pidrum.BEAT_LEVELS[1]["bpm"])

    def test_beat_levels_tempo_and_eighths(self):
        self.h.goto(pidrum.Beat)
        m = self.h.app.mode
        bpms = []
        for lv in range(len(pidrum.LEVELS)):
            m.level = lv
            m.setup(self.h.t)
            bpms.append(60.0 / m.period)
            self.assertLessEqual(m.window, m.step / 2 + 1e-9)
        self.assertEqual(bpms, sorted(bpms))
        self.assertEqual(m.sub, 2)
        # Croches : un pas sur deux tombe entre deux temps
        n = 1
        self._beat_hit_at(m, n, 0.0) if m.target(n) else None

    def test_beat_misses_drop_level_only_when_child_plays(self):
        self.h.goto(pidrum.Beat)
        m = self.h.app.mode
        m.home = m.level = 2
        m.setup(self.h.t)
        # Enfant absent : aucune redescente
        self.h.run(pidrum.BEAT_MISSES_TO_DROP * m.step + 2 * m.period + 1.0)
        self.assertEqual(m.level, 2)
        # Enfant qui tape a cote a chaque temps : redescend d'un niveau
        for _ in range(pidrum.BEAT_MISSES_TO_DROP + 4):
            self.h.run(m.step)
            self.h.hit("orange" if "orange" not in m.patterns[m.pattern_idx] else "green")
            if m.level < 2:
                break
        self.assertEqual(m.level, 1)
        self.assertEqual(m.home, 2)

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

    # --- boutons de facade et niveaux ---

    def test_held_button_pads_change_level_and_mode(self):
        app = self.h.app
        self.hold("yellow", sec=pidrum.BUTTON_HOLD + 0.1)        # triangle
        self.assertEqual((app.mode.home, app.mode.level), (1, 1))
        self.assertIsInstance(app.mode, pidrum.FreePlay)
        self.hold("green", sec=pidrum.BUTTON_HOLD + 0.1)         # X
        self.assertEqual(app.mode.home, 0)
        self.hold("green", sec=pidrum.BUTTON_HOLD + 0.1)         # deja au plus facile
        self.assertEqual(app.mode.home, 0)
        self.hold("red", sec=pidrum.BUTTON_HOLD + 0.1)           # rond
        self.assertIsInstance(app.mode, pidrum.CopyMe)
        self.hold("blue", sec=pidrum.BUTTON_HOLD + 0.1)          # carre
        self.assertIsInstance(app.mode, pidrum.FreePlay)
        self.hold("blue", sec=pidrum.BUTTON_HOLD + 0.1)
        self.assertIsInstance(app.mode, pidrum.Song)

    def test_hits_and_short_presses_are_not_buttons(self):
        app = self.h.app
        for _ in range(20):
            for pad in pidrum.GAME_PADS:
                self.h.hit(pad)
            self.h.run(0.1)
        self.hold("yellow", sec=pidrum.BUTTON_HOLD * 0.6)
        self.h.run(1.0)
        self.assertIsInstance(app.mode, pidrum.FreePlay)
        self.assertEqual(app.mode.home, 0)

    def test_one_action_per_press_and_hit_still_counts(self):
        app = self.h.app
        calls = []
        app.mode.on_hit = lambda a, pad, t: calls.append(pad)
        self.hold("yellow", sec=pidrum.BUTTON_HOLD * 3)
        self.assertEqual(app.mode.home, 1)       # une seule marche malgre 3x la duree
        self.assertEqual(calls, ["yellow"])      # le coup de depart reste une frappe

    def test_bounce_during_hold_keeps_hold(self):
        app = self.h.app
        t = self.h.t
        app.handle("down", "yellow", t)
        app.handle("up", "yellow", t + 0.002)
        app.handle("down", "yellow", t + 0.004)  # rebond : ignore pour le son
        self.h.run(pidrum.BUTTON_HOLD + 0.1)
        app.handle("up", "yellow", self.h.t)
        self.assertEqual(app.mode.home, 1)

    def test_direct_buttons_and_disabled_pad(self):
        app = self.h.app
        played = []
        app.play = lambda name, volume=1.0, loops=0: played.append(name)
        self.hold("triangle", sec=pidrum.BUTTON_HOLD + 0.1)
        self.assertEqual(app.mode.home, 1)
        self.assertIn("level_up", played)
        # Cymbale jaune desactivee : le bouton triangle (meme code) marche encore, sans son
        app.disabled = {"yellow"}
        played.clear()
        self.hold("yellow", sec=pidrum.BUTTON_HOLD + 0.1)
        self.assertEqual(app.mode.home, 2)
        self.assertNotIn("hihat", played)

    def test_select_held_blocks_buttons(self):
        self.hold("select", "yellow", sec=pidrum.BUTTON_HOLD + 0.1)
        self.assertEqual(self.h.app.mode.home, 0)

    def test_level_tables_complete(self):
        for table in (pidrum.FREE_LEVELS, pidrum.COPY_LEVELS, pidrum.BEAT_LEVELS, pidrum.SONG_LEVELS):
            self.assertEqual(len(table), len(pidrum.LEVELS))
        self.assertEqual(len(pidrum.LEVEL_COLORS), len(pidrum.LEVELS))
        for c in pidrum.BEAT_LEVELS:
            for p in c["patterns"]:
                self.assertTrue(set(p) <= set(pidrum.PATTERN_PADS) | {"."}, p)

    def test_free_play_groove_loops_and_stops(self):
        app = self.h.app
        started, stopped = [], []

        class FakeChannel:
            def stop(self):
                stopped.append(True)
        app.play = lambda name, volume=1.0, loops=0: started.append((name, loops)) or FakeChannel()
        self.hold("yellow", sec=pidrum.BUTTON_HOLD + 0.1)
        self.assertIn(("groove_" + pidrum.FREE_LEVELS[1], -1), started)
        self.hold("red", sec=pidrum.BUTTON_HOLD + 0.1)
        self.assertEqual(stopped, [True])

    def test_levels_persist(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "progress.json")
        pygame.quit()
        app = pidrum.App(window=True, use_kit=False, progress_file=path)
        app.switch_mode(app.last_t, 2)                            # Le beat
        app.change_level(1, app.last_t)
        app.change_level(1, app.last_t)
        pygame.quit()
        app = pidrum.App(window=True, use_kit=False, progress_file=path)
        self.assertEqual({m.key: m.home for m in app.modes}, {"free": 0, "copy": 0, "beat": 2, "song": 0})
        with open(path, "w") as f:
            f.write("{pas du json")
        pygame.quit()
        app = pidrum.App(window=True, use_kit=False, progress_file=path)   # ne plante pas
        self.assertEqual(app.modes[2].home, 0)

    def test_energy_only_goes_up_and_triggers_star_power(self):
        app = self.h.app
        self.h.goto(pidrum.CopyMe)
        m = app.mode
        hits = 0
        while not app.star_power(self.h.t):
            before = app.energy
            while m.state != "wait":
                self.h.run(0.1)
            wrong = next(p for p in pidrum.GAME_PADS if p != m.seq[m.idx])
            self.h.hit(wrong)
            self.assertGreaterEqual(app.energy, before)  # une erreur ne retire rien
            self.copy_win(m)
            hits += 1
            self.assertLess(hits, 40)
        self.h.run(pidrum.STAR_TIME + 0.1)
        self.assertFalse(app.star_power(self.h.t))

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
        m.home = m.level = 3
        m.setup(h.t)
        h.run(pidrum.COPY_LEAD + m.step * 0.5)
        h.shot("2_copy_show")
        h.run(m.step * len(m.seq) + m.hint + 0.5)
        h.shot("2_copy_wait_target")
        h.hit(m.seq[0])
        h.run(0.25)
        h.shot("2_copy_celebrate")
        h.goto(pidrum.Beat)
        b = h.app.mode
        h.app.handle("down", "yellow", h.t)                       # triangle maintenu
        h.run(pidrum.BUTTON_HOLD * 0.7)
        h.shot("3_beat_button_gauge")
        h.run(pidrum.BUTTON_HOLD * 0.3 + 0.05)
        h.app.handle("up", "yellow", h.t)
        h.run(0.4)
        h.shot("3_beat_level_banner")
        b.home = b.level = 4
        b.setup(h.t)
        h.run(b.t0 + 2.6 * b.period - h.t)
        h.shot("3_beat_approach")
        h.run(b.t0 + 3 * b.period - h.t)
        h.hit(b.target(3))
        h.run(0.1)
        h.shot("3_beat_reward")
        h.goto(pidrum.Song)
        s = h.app.mode
        while s.state != "play":
            h.run(0.1)
        h.run(s.notes[2]["t"] - h.t - 0.6)
        h.hit(s.notes[1]["pad"]) if abs(s.notes[1]["t"] - h.t) < s.window else None
        h.shot("5_song_track")
        h.t = s.notes[2]["t"]
        h.hit(s.notes[2]["pad"])
        h.run(0.1)
        h.shot("5_song_hit")
        for _ in range(int(1 / pidrum.ENERGY_HIT) + 1):
            h.app.gain_energy(pidrum.ENERGY_HIT, h.t)
        h.run(0.5)
        h.shot("5_song_star_power")
        h.goto(pidrum.FreePlay)
        old = pidrum.SHUTDOWN_CMD
        pidrum.SHUTDOWN_CMD = [sys.executable, "-c", "pass"]
        try:
            h.app.handle("down", "select", h.t)
            h.app.handle("down", "start", h.t)
            h.run(pidrum.SHUTDOWN_HOLD * 0.6)
            h.shot("4_shutdown_gauge")
            h.run(pidrum.SHUTDOWN_HOLD * 0.4 + 0.1 + pidrum.SHUTDOWN_FADE * 0.3)
            h.shot("4_shutdown_fade")
        finally:
            pidrum.SHUTDOWN_CMD = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
