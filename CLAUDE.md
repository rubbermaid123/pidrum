# CLAUDE.md - PiDrum

Batterie d'apprentissage pour un enfant de 3 ans qui ne lit pas encore. Un vieux kit de batterie PS3 est branché sur un Raspberry Pi 4, avec l'image et le son sur une TV.

## Contexte matériel (à connaître avant de toucher au code)

- Le kit est un **Guitar Hero 5 voies** (World Tour ou GH5/Warriors of Rock), **pas un Rock Band** malgré la description du projet. Il comprend 3 pads (rouge, bleu, vert), 2 cymbales (jaune, orange) et 1 pédale de grosse caisse. Le boîtier central porte START, SELECT et PS.
- Le kit se connecte par un dongle USB sans fil propriétaire, que l'utilisateur possède. Sous Linux, il apparaît comme un périphérique HID joystick lisible via **evdev** (`/dev/input/event*`).
- Le kit réel est un **GH World Tour PS3**, USB `12ba:0120`, nom evdev `RedOctane MIDI Drum GuitarHero for Playstation (R) 3`, pilote `hid-generic`. Mapping relevé le 2026-10-25 (un bouton par élément, aucune combinaison) :

  | Élément | Code | Statut |
  |---|---|---|
  | rouge | `BTN_C` (306) | vérifié |
  | bleu | `BTN_A` (304) | vérifié |
  | vert | `BTN_B` (305) | vérifié, **rebond ~60 ms** à chaque coup |
  | orange | `BTN_Z` (309) | vérifié |
  | jaune | `BTN_NORTH` (307) | **déduit** (connecteur brisé, à réparer) |
  | pédale | `BTN_WEST` (308) | **déduit** (muette au mapping) |
  | START | `BTN_TL2` (312) | vérifié |
  | SELECT | `BTN_TR2` (313) | vérifié |

  Les codes déduits suivent la disposition PS3 standard (triangle, L1), cohérente avec les éléments vérifiés. `pads.json` les liste dans `unverified`. `map_pads.py` reste l'outil de référence. Ne jamais coder en dur des codes evdev dans `pidrum.py`. Un rôle peut aussi être une combinaison `[code, code]`.
- Le kit s'appaire avec un délai après la mise sous tension : le dongle envoie des rapports « repos » (hat 0x0f, axes 0x80) tant que le kit ne transmet pas.
- **Aucun axe ABS ne bouge pendant les frappes** : pas de vélocité exposée par `hid-generic` pour ce kit. Une vélocité éventuelle demanderait de lire `/dev/hidraw*` directement.

## Plateforme cible

- Raspberry Pi 4 (IP 192.168.2.52, utilisateur `pidrum`), **Raspberry Pi OS Lite 64-bit**, sans bureau. Base Debian 13 Trixie (Python 3.13) ; Bookworm reste compatible.
- Affichage : pygame/SDL2 en `SDL_VIDEODRIVER=kmsdrm`, plein écran natif
- Audio : `SDL_AUDIODRIVER=alsa`, sortie HDMI (TV en mode Jeu) ou jack 3,5 mm
- Démarrage : service systemd `pidrum.service` (placeholder `__USER__` remplacé par sed), `Nice=-10`
- **Dépendances uniquement via apt** : `python3-pygame python3-numpy python3-evdev`. Pas de pip ni de venv sur le Pi. N'ajouter aucune dépendance qui n'existe pas en paquet Debian arm64 (Bookworm et Trixie).

## Fichiers

| Fichier | Rôle |
|---|---|
| `pidrum.py` | Application : boucle principale, entrée evdev, rendu, 3 modes |
| `sounds.py` | Synthèse numpy de tous les sons (batterie, jingle, ding, métronome) |
| `map_pads.py` | Outil CLI de mapping, frapper chaque élément 3 fois, produit `pads.json` ; `analyze()` est testable sans evdev |
| `test_pidrum.py` | Tests headless unittest + captures PNG dans `shots/` |
| `pidrum.service` | Unité systemd |
| `README.md` | Procédure d'installation pour l'utilisateur |
| `pads.json` | Généré sur le Pi, non versionné |

## Architecture de `pidrum.py`

- **Réglages** : constantes en tête de fichier (`BPM`, `BEAT_WINDOW`, `BEAT_GOAL`, `JUDGE_OFFSET`, `HOLD_TO_SWITCH`, `DEBOUNCE`, `KICK_IN_GAMES`, `SIMON_MAX_LEN`, `COPY_*`, `BEAT_LEAD_IN`, etc.).
- **`PADS`** : nom → couleur, position normalisée (x, y), forme (`drum`, `cymbal` ou `bar`) et son. La disposition à l'écran reproduit le kit vu par l'enfant.
- **`KeyDecoder`** : traduit les événements evdev bruts en `("down"|"up", role)`, trame par trame (SYN_REPORT). Gère les combinaisons : la plus spécifique gagne.
- **`EvdevInput`** : **thread dédié** bloqué sur `select()`. Il ouvre le périphérique par nom (`device_name` de `pads.json`), se reconnecte toutes les `RECONNECT_EVERY` s et appelle `App.kit_event(kind, role, t)`.
- **Chemin d'une frappe** : `strike()` (debounce sous verrou + `play()`, appelé directement depuis le thread kit, son environ 1 ms après la frappe) puis `react()` (visuel + `mode.on_hit()`, thread principal, via `kit_queue`, avec le temps `t` de la frappe). `App.handle()` enchaîne les deux pour le clavier et les tests. **Ne jamais remettre le déclenchement du son dans la boucle de rendu** : `display.flip()` sous kmsdrm peut bloquer jusqu'au vsync (16 ms).
- **Boucle `App.run()`** : rendu seul, cadencé à `FPS`. `sys.setswitchinterval(0.001)` pour que le thread kit récupère le GIL vite.
- **Modes** : sous-classes de `Mode` qui implémentent `enter`, `on_hit`, `update` et `draw`, avec `dots` qui fixe le nombre de points de la bannière.
  - `FreePlay` : jeu libre, avec une invitation visuelle après `IDLE_HINT_AFTER` secondes sans frappe.
  - `CopyMe` : type Simon, avec les états `show`, `wait` et `pause`. Difficulté adaptative de longueur 1 à 4.
  - `Beat` : métronome avec des anneaux d'approche qui se referment sur le pad pile sur le temps. Les motifs sont dans `PATTERNS` et le niveau suivant arrive après `BEAT_GOAL` réussites.
- **API d'effets** pour les modes : `app.demo(pad, t)`, `app.reward(pad, t)`, `app.celebrate(t)`, `app.ring(pad, t)`, `app.burst(pad)`, `app.draw_target(surf, pad, t)`, `app.draw_approach(surf, pad, frac)`, `app.play(name, volume)`.
- **Changement de mode** : appui long sur START (`HOLD_TO_SWITCH`). Les appuis courts sont ignorés exprès, parce que l'enfant tape sur le boîtier.

## Règles de conception (non négociables)

1. **Aucun texte à l'écran.** L'utilisateur final a 3 ans. Tout passe par la couleur, la forme, le son et l'animation.
2. **Aucun retour négatif.** Il n'y a pas de son d'échec, pas d'écran rouge, pas de « perdu ». Une erreur montre simplement la bonne réponse.
3. **La latence audio prime sur tout le reste.** Toute fonctionnalité qui ajoute du délai entre la frappe et le son est refusée.
4. **Tolérance généreuse.** Fenêtres de timing larges, difficulté qui redescend toute seule.
5. **La pédale est exclue des jeux par défaut** (`KICK_IN_GAMES = False`), parce que l'enfant n'atteint pas forcément la pédale.
6. **Rendu léger** : formes pygame simples, pas de surfaces alpha plein écran. Cible : 60 FPS en 1080p sur Pi 4.

## Conventions de code

- **ASCII uniquement dans les scripts** (`.py`, `.service`) : commentaires en français **sans accents**. Les fichiers `.md` peuvent contenir des accents. Vérification : `grep -nP '[^\x00-\x7F]' *.py *.service`
- Python 3.11+, bibliothèque standard + pygame, numpy et evdev seulement.
- Messages console en français sans accents.
- Pas de valeurs magiques dans les modes : passer par les constantes en tête de fichier.

## Tests

Pas de kit dans l'environnement de dev. Tout est dans `test_pidrum.py` (unittest, pilotes SDL factices, temps synthétique via `Harness` et `App.reset_clock(t)`) :

```
python3 test_pidrum.py
```

Couvert : sons finis et ≤ 1.0, attaque immédiate, format du mixer ; appui long/court sur START (clavier et kit) ; debounce ; `KeyDecoder` (combinaisons, appui et relâchement dans la même trame) ; thread evdev avec faux périphérique (lecture, déconnexion, reconnexion) ; `map_pads.analyze` ; `CopyMe` (mauvaise frappe n'avance pas `idx`, 3 réussites → longueur 2, pas de pédale) ; `Beat` (8 frappes → `level` 1, hors fenêtre ou mauvais pad non comptés, double frappe comptée une fois) ; particules bornées. Captures PNG de chaque mode dans `shots/` : les inspecter après tout changement visuel.

En local sous Windows, pygame n'a pas de wheel pour Python 3.14 : tester avec `pygame-ce` (même API) dans un venv local. Le Pi utilise le paquet apt `python3-pygame`.

Lancement local en fenêtre, au clavier (1 à 5 pour les pads, espace pour la pédale, Entrée pour START, Échap pour quitter) :

```
python3 pidrum.py --window --keyboard-only
```

## Déploiement

L'utilisateur préfère des **commandes individuelles d'une ligne**, une par bloc, faciles à copier-coller en SSH. Pas de gros blocs de script.

```
scp pidrum.py sounds.py map_pads.py test_pidrum.py pidrum.service <user>@<ip-du-pi>:~/pidrum/
```
```
sudo systemctl restart pidrum
```
```
journalctl -u pidrum -f
```

## Backlog

1. **Valider le mapping** sur le vrai kit (les combinaisons sont gérées, reste à confirmer ce qu'envoie le kit).
2. **Mesurer la latence réelle** sur le Pi (thread kit vs vsync kmsdrm, `AUDIO_BUFFER` 256 sur HDMI/jack).
3. **Vélocité** : volume (`channel.set_volume`) selon l'axe ABS associé à chaque pad, une fois les axes connus. Prévoir un champ optionnel `vel_abs` par pad dans `pads.json`.
4. **Vrais échantillons .wav** en option, avec repli sur la synthèse si les fichiers sont absents.
5. **Mode « chanson »** : accompagnement musical simple (comptines) sur lequel taper le beat.
6. **Calibration de latence** : un mode parent pour mesurer et régler `JUDGE_OFFSET`.
7. **Extinction propre** : combinaison longue SELECT + START pour `systemctl poweroff`, afin de protéger la carte SD.

## Interlocuteur

L'utilisateur est architecte infrastructure senior. Réponses techniques et directes, en français.
