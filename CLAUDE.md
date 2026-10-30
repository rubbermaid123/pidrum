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
- **Boutons de façade** (carré, triangle, rond, X sur le boîtier) : sur le GH WT PS3 ils envoient le **même code que le pad de leur couleur** (rond = rouge `BTN_C`, carré = bleu `BTN_A`, X = vert `BTN_B`, triangle = jaune `BTN_NORTH`), d'où le mapping relevé. Non vérifié sur le kit réel : déduit de la convention PS3. On les distingue des frappes par l'**appui long** (`BUTTON_HOLD`). Si le kit envoie des codes propres, `map_pads.py` les écrit dans `pads.json` → `"buttons"`.
- Le kit s'appaire avec un délai après la mise sous tension : le dongle envoie des rapports « repos » (hat 0x0f, axes 0x80) tant que le kit ne transmet pas.
- **Vélocité** : aucun axe ABS ne bouge, mais le rapport HID brut (27 octets, `/dev/hidrawN`) porte une pression 0-127 par pad : rouge octet 12, vert 13, bleu 14, orange 16 (jaune et pédale inconnus). Coups doux ≈ 28-40, forts ≈ 80-122. Diaphonie observée : un coup fort sur le bleu fait apparaître ~33 sur le vert sans bouton. Accès via `99-pidrum-hidraw.rules` (groupe `input`).

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
| `sounds.py` | Synthèse numpy de tous les sons (batterie, jingle, ding, métronome, au revoir) |
| `songs.py` | Comptines du domaine public : mélodies, `chart()` par densité, `render()` de la musique ; accompagnements du jeu libre (`GROOVES`, `render_groove()` bouclable) |
| `pidrum-poweroff.sudoers` | Règle sudoers limitée à `systemctl poweroff` |
| `docs/images/` | Captures pour le README (copiées depuis `shots/`) |
| `map_pads.py` | Outil CLI de mapping, frapper chaque élément 3 fois, puis (facultatif) les boutons de façade, produit `pads.json` ; `analyze()` et `analyze_buttons()` sont testables sans evdev |
| `test_pidrum.py` | Tests headless unittest + captures PNG dans `shots/` |
| `pidrum.service` | Unité systemd |
| `README.md` | Procédure d'installation pour l'utilisateur |
| `pads.json` | Généré sur le Pi, non versionné |
| `progress.json` | Niveau choisi par jeu, écrit par `pidrum.py` (seulement en vrai lancement, pas dans les tests), non versionné |

## Architecture de `pidrum.py`

- **Réglages** : constantes en tête de fichier (`JUDGE_OFFSET`, `HOLD_TO_SWITCH`, `DEBOUNCE`, `KICK_IN_GAMES`, `BUTTON_*`, `COPY_*`, `BEAT_*`, `SONG_*`, `ENERGY_*`, `HIGHWAY_*`, etc.). Les réglages de difficulté sont dans des **tables par niveau** : `FREE_LEVELS`, `COPY_LEVELS`, `BEAT_LEVELS`, `SONG_LEVELS`, une entrée par élément de `LEVELS` (apprenti, novice, debutant, intermediaire, avance, expert). `"drums"` dans une table = peaux seulement (`DRUM_PADS`), via `lane_pool()`.
- **`PADS`** : nom → couleur, forme (`drum`, `cymbal` ou `bar`) et son. Les pistes (`LANES` = `GAME_PADS`) suivent l'ordre Guitar Hero World Tour, qui est aussi l'ordre gauche → droite du kit vu par l'enfant. La pédale est une barre en travers de l'autoroute.
- **Scène façon Guitar Hero / Rock Band** : `layout()` calcule une autoroute en perspective. `project(u, z)` donne (x, y, échelle) avec `u` de -1 à 1 en travers et `z` = 0 sur la ligne de frappe, 1 au fond ; une note à `dt` secondes est à `z = dt / lead`. `app.geo[pad]` = cible sur la ligne de frappe (cx, cy, rx, ry). Le décor fixe (dégradé, faisceaux, enceintes, autoroute, séparateurs) est pré-rendu dans `self.bg` (un seul blit par image). Dynamique : projecteurs, pistes allumées, lignes de temps, bords (énergie, bleus en star power), cibles (`draw_fret`), gemmes (`draw_gem`), particules (dont flammes à gravité négative, clé `grav`), foule, interface.
- **Énergie / star power** : `gain_energy()` (bonne frappe, ou toute frappe en jeu libre) ; la jauge ne descend jamais ; pleine → `STAR_TIME` s de star power (sons `star` + `cheer`).
- **`KeyDecoder`** : traduit les événements evdev bruts en `("down"|"up", role)`, trame par trame (SYN_REPORT). Gère les combinaisons : la plus spécifique gagne.
- **`EvdevInput`** : **thread dédié** bloqué sur `select()`. Il ouvre le périphérique par nom (`device_name` de `pads.json`), se reconnecte toutes les `RECONNECT_EVERY` s et appelle `App.kit_event(kind, role, t)`.
- **Force de frappe** : si `pads.json` a `velocity.report_bytes`, le thread kit ouvre aussi le hidraw du kit (`HidRaw.find` par nom) et fait `select()` sur les deux fd. Le noyau livre le rapport hidraw **avant** les événements evdev du même rapport : on lit hidraw d'abord, `VelocityDecoder` retient la valeur au front montant (pas de max cumulé, à cause de la diaphonie), et `take(role)` l'attache au `down` evdev si elle a moins de `VEL_FRESH`. `velocity_gain()` donne un niveau relatif au son normal, de `VEL_SOFT_GAIN` (0,9) à `VEL_HARD_GAIN` (1,5). `set_volume` plafonne à 1.0 : au-delà, `play_pad()` joue la variante `<son>_loud` pré-calculée par `sounds.make_loud` (gain simple si le pic le permet, sinon saturation tanh minimale trouvée par dichotomie, gain réel dans `sounds.LOUD_ACHIEVED`). Hidraw illisible → son normal à 1.0.
- **Chemin d'une frappe** : `strike()` (debounce sous verrou + `play()`, appelé directement depuis le thread kit, son environ 1 ms après la frappe) puis `react()` (visuel + `mode.on_hit()`, thread principal, via `kit_queue`, avec le temps `t` de la frappe). `App.handle()` enchaîne les deux pour le clavier et les tests. **Ne jamais remettre le déclenchement du son dans la boucle de rendu** : `display.flip()` sous kmsdrm peut bloquer jusqu'au vsync (16 ms).
- **Boucle `App.run()`** : rendu seul, cadencé à `FPS`. `sys.setswitchinterval(0.001)` pour que le thread kit récupère le GIL vite.
- **Modes** : sous-classes de `Mode` avec `key` (clé dans `progress.json`), `dots` (points de la bannière) et `LEVELS` (table de niveau). `home` = niveau choisi par le parent, `level` = niveau joué ; `auto_down()` redescend seul (règle 4), `auto_up()` remonte sans dépasser `home`. `cfg()` = réglages du niveau joué.
  - `FreePlay` : jeu libre ; accompagnement `groove_<id>` en boucle (`loops=-1`) selon le niveau, lignes de temps à son tempo ; invitation visuelle après `IDLE_HINT_AFTER` secondes sans frappe.
  - `CopyMe` : type Simon, états `show`, `wait` et `pause`. En `show`, les gemmes descendent (`COPY_LEAD`) et `demo()` joue chaque pad quand sa gemme touche la ligne. Longueur adaptative entre `min` et `max` du niveau ; au bout de la plage, le niveau joué change.
  - `Beat` : grille de pas (`sub` pas par temps, `"."` = silence dans les motifs `BEAT_LEVELS`, lettres → `PATTERN_PADS`). Fenêtre bornée à un demi-pas. Après `goal` réussites : motif suivant ; après tous les motifs : `auto_up()`. `BEAT_MISSES_TO_DROP` gemmes ratées d'affilée **alors que l'enfant joue** (`BEAT_ACTIVE`) → `auto_down()`.
  - `Song` : états `wait`, `play` et `pause`. La musique (`song_<id>`, synthétisée par `songs.py` au démarrage) part dans `begin_play()` et les notes sont calées sur cet instant réel, après `songs.COUNT_IN` temps de décompte (doit rester ≥ le plus grand `lead` de `SONG_LEVELS`). Partition via `songs.chart(song, density, order)` (densité 0 = une note par mesure … 4 = toutes + accords de cymbale ; hauteur → pad selon `SONG_PAD_ORDER`, limité aux pads du niveau). Pas d'échec : fin de chanson = `celebrate`, niveau ajusté selon `SONG_LEVEL_UP` / `SONG_LEVEL_DOWN` dans `[0, home]`. `leave()` coupe la musique.
- **Hooks de mode** : `draw_back` (sur l'autoroute, sous les cibles : lignes de temps, pistes allumées), `draw` (gemmes, cibles attendues), `draw_hud` (progression) et `leave` (changement de mode ou de niveau, extinction) en plus de `enter`, `on_hit` et `update`.
- **API d'effets** pour les modes : `app.demo(pad, t)`, `app.reward(pad, t)`, `app.celebrate(t)`, `app.ring(pad, t)`, `app.burst(pad)`, `app.gain_energy(x, t)`, `app.draw_gem(surf, pad, z, fade)`, `app.light_lane(surf, pad, k)`, `app.draw_beat_lines(surf, t, t0, period, lead)`, `app.draw_target(surf, pad, t)`, `app.draw_pips(surf, total, done)`, `app.draw_progress(surf, p)`, `app.play(name, volume, loops)`.
- **Changement de mode** : appui long sur START (`HOLD_TO_SWITCH`), ou boutons rond / carré maintenus. Les appuis courts sont ignorés exprès, parce que l'enfant tape sur le boîtier. Si SELECT est tenu, START ne change pas de mode et les boutons de façade ne font rien.
- **Boutons de façade** : `strike()` renvoie `"hit"`, `"hold"` (rebond ou pad désactivé qui sert de bouton : suivi d'appui sans son), `"up"`, `"ctl"` ou `None` ; la file `kit_queue` transporte ce verdict jusqu'à `react()`. `react()` note le début d'appui dans `held` (pads de `button_of` et boutons directs `triangle`…), le relâchement l'efface. `update()` déclenche `BUTTON_ACTIONS` une seule fois quand un appui dépasse `BUTTON_HOLD`. Les pads relâchés envoient donc maintenant un `up` jusqu'à `react()`.
- **Niveaux** : `change_level(±1)` (triangle / X) fixe `home = level`, relance le mode, joue `level_up` / `level_down`, affiche le grand escalier et écrit `progress.json` (`save_progress`, seulement si `App(progress_file=...)`, ce que fait `main()`).
- **Extinction** : SELECT + START tenus `SHUTDOWN_HOLD` s → `begin_shutdown()` (son `bye`, fondu `draw_shutdown`) → après `SHUTDOWN_FADE` s, `request_poweroff()` lance `SHUTDOWN_CMD` (`sudo -n systemctl poweroff`, autorisé par `pidrum-poweroff.sudoers`). En cas de refus : message console, le jeu reprend (aucun retour négatif).
- **Éléments désactivés** : `pads.json` → `"disabled": [...]` → `App.disabled`. Non dessinés, sans son, exclus de `App.target_pads()` (seule source des pads demandés par les modes) et des motifs de `Beat`.

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

Couvert : sons finis et ≤ 1.0, attaque immédiate, format du mixer ; accompagnements bouclables de longueur exacte ; densités de partition croissantes et accords en expert ; appui long/court sur START (clavier et kit) ; debounce ; `KeyDecoder` (combinaisons, appui et relâchement dans la même trame) ; thread evdev avec faux périphérique (lecture, déconnexion, reconnexion) ; `map_pads.analyze` et `analyze_buttons` ; boutons de façade (appui long par pad partagé ou bouton direct, une action par appui, rebond pendant l'appui, frappes courtes ignorées, pad désactivé, SELECT tenu) ; tables de niveaux complètes ; persistance de `progress.json` ; `CopyMe` (mauvaise frappe n'avance pas `idx`, 3 réussites → longueur +1, apprenti = peaux, remontée plafonnée à `home`, erreurs → raccourcit puis redescend) ; `Beat` (`goal` frappes → motif suivant, tous les motifs → niveau suivant, tempo croissant, ratés seulement si l'enfant joue, hors fenêtre ou mauvais pad non comptés, double frappe comptée une fois) ; `Song` (niveaux, plafond) ; énergie et star power ; particules bornées. Captures PNG de chaque mode dans `shots/` : les inspecter après tout changement visuel.

En local sous Windows, pygame n'a pas de wheel pour Python 3.14 : tester avec `pygame-ce` (même API) dans un venv local. Le Pi utilise le paquet apt `python3-pygame`.

Lancement local en fenêtre, au clavier (1 à 5 pour les pads, espace pour la pédale, Entrée pour START, flèches maintenues pour les boutons de façade, Échap pour quitter) :

```
python3 pidrum.py --window --keyboard-only
```

## Déploiement

L'utilisateur préfère des **commandes individuelles d'une ligne**, une par bloc, faciles à copier-coller en SSH. Pas de gros blocs de script.

```
scp pidrum.py sounds.py songs.py map_pads.py test_pidrum.py pidrum.service <user>@<ip-du-pi>:~/pidrum/
```
```
sudo systemctl restart pidrum
```
```
journalctl -u pidrum -f
```

## Backlog

1. **Valider le mapping** sur le vrai kit (les combinaisons sont gérées, reste à confirmer ce qu'envoie le kit), y compris les **boutons de façade** : même code que les pads (supposé) ou codes propres (`map_pads.py`). Si l'appui d'un bouton porte une force nulle dans hidraw, on pourrait aussi couper le son du pad.
2. **Mesurer la latence réelle** sur le Pi (thread kit vs vsync kmsdrm, `AUDIO_BUFFER` 256 sur HDMI/jack).
3. **Vélocité, suite** : relever les octets de force de la cymbale jaune et de la pédale après réparation ; faire détecter les octets par `map_pads.py` (aujourd'hui relevés à la main).
4. **Vrais échantillons .wav** en option, avec repli sur la synthèse si les fichiers sont absents.
5. **Chansons de l'utilisateur** : charger un fichier audio + une partition (JSON de temps/pads) en plus des comptines synthétisées.
6. **Calibration de latence** : un mode parent pour mesurer et régler `JUDGE_OFFSET`.
7. **Performance de la scène** sur le Pi 4 : mesurer les FPS avec l'autoroute (même coût que l'ancien rendu sur PC, ≈ 1,8 ms/image) ; réduire `CROWD_SIZE` ou les pistes allumées si besoin.

## Interlocuteur

L'utilisateur est architecte infrastructure senior. Réponses techniques et directes, en français.
