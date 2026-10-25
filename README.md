# PiDrum

Batterie d'apprentissage pour jeune enfant : kit Guitar Hero (PS3) + Raspberry Pi 4 + TV.
Aucun texte à l'écran, tout passe par les couleurs, les sons et les animations.

## Fichiers

- `pidrum.py` : l'application (3 modes)
- `sounds.py` : synthèse des sons de batterie (aucun fichier audio requis)
- `map_pads.py` : détection des boutons du kit, génère `pads.json`
- `pidrum.service` : démarrage automatique au boot
- `test_pidrum.py` : tests headless (aucun kit ni écran requis), captures dans `shots/`

## Modes

Appui **long** (1,5 s) sur START du kit pour passer au mode suivant. Une jauge apparaît en haut à droite pendant l'appui, puis 1, 2 ou 3 gros points indiquent le mode choisi. Un appui court est ignoré, donc l'enfant ne change pas de mode par accident.

1. **Jeu libre** : chaque pad joue son son et s'illumine. Après 20 s sans frappe, un pad « appelle » doucement l'enfant.
2. **Copie-moi** : le jeu joue un pad (ou une séquence) et l'enfant le reproduit. Le pad attendu clignote en blanc. Après 3 réussites, la séquence s'allonge (max 4). Après plusieurs erreurs, elle raccourcit. Il n'y a jamais de son d'échec.
3. **Le beat** : métronome à 70 BPM. Un anneau blanc se referme sur le pad à frapper et le touche exactement sur le temps. Chaque bonne frappe remplit un point en haut. À 8 points, une fanfare joue et le motif suivant commence (une couleur, puis deux en alternance, etc.).

Le clavier fonctionne aussi pour les tests : `1` à `5` pour les pads, espace pour la pédale, Entrée pour START et Échap pour quitter.

## Installation sur le Pi

Installer **Raspberry Pi OS Lite (64-bit)** avec Raspberry Pi Imager en activant SSH. La version actuelle est basée sur Debian 13 (Trixie). Bookworm fonctionne aussi. Ensuite, sur le Pi :

```
sudo apt update
```
```
sudo apt install -y python3-pygame python3-numpy python3-evdev evtest
```
```
sudo usermod -aG input,video,render,audio $USER
```
```
mkdir -p ~/pidrum
```

Se déconnecter puis se reconnecter en SSH pour que les groupes s'appliquent, puis vérifier :

```
groups
```

Depuis ton poste, copier les fichiers :

```
scp pidrum.py sounds.py map_pads.py test_pidrum.py pidrum.service <user>@<ip-du-pi>:~/pidrum/
```

Vérifier que tout fonctionne sur le Pi, sans kit ni écran (arrêter le service avant s'il tourne déjà) :

```
cd ~/pidrum && python3 test_pidrum.py
```

## Mapping du kit (une seule fois)

Brancher le dongle, allumer le kit, puis :

```
cd ~/pidrum && python3 map_pads.py
```

Le script demande de frapper chaque élément 3 fois, puis d'appuyer sur START et SELECT. Il affiche les touches vues pour chaque frappe, puis écrit `pads.json`.

- Si un élément n'a pas de bouton à lui (kit qui envoie couleur + modificateur), le script enregistre la **combinaison** de boutons, par exemple `"yellow": [304, 401]`. `pidrum.py` sait la lire. C'est signalé sous « ATTENTION » : vérifier en jeu libre que chaque élément joue le bon son.
- S'il reste un conflit (même combinaison pour deux éléments), envoie-moi la sortie de `sudo evtest` pendant que tu frappes les pads.

La sortie liste aussi les axes analogiques qui bougent pendant les frappes. On s'en servira pour gérer la vélocité (nuances) dans une prochaine version.

## Test manuel

Depuis SSH, sans bureau :

```
cd ~/pidrum && SDL_VIDEODRIVER=kmsdrm python3 pidrum.py
```

Ctrl+C pour arrêter.

## Audio et latence

C'est le point critique pour un instrument de percussion.

- Le kit est lu dans un thread dédié qui déclenche le son dès la frappe, indépendamment du rendu. L'attente de synchronisation verticale de l'écran n'ajoute donc pas de délai.
- Lister les sorties disponibles : `aplay -L`
- Choisir la sortie par défaut : `sudo raspi-config` → System Options → Audio
- **HDMI** : mettre la TV en **mode Jeu**. Sinon, son traitement interne ajoute facilement 50 à 100 ms et le décalage se sent.
- **Jack 3,5 mm vers une petite enceinte** : c'est souvent la meilleure latence. La TV ne sert alors qu'à l'image.
- En cas de craquements, monter `AUDIO_BUFFER` de 256 à 512 en haut de `pidrum.py`.
- Si les frappes semblent jugées « en retard » dans *Le beat*, ajuster `JUDGE_OFFSET` (par exemple `0.05`).

## Démarrage automatique

```
sed -i "s/__USER__/$USER/g" ~/pidrum/pidrum.service
```
```
sudo cp ~/pidrum/pidrum.service /etc/systemd/system/
```
```
sudo systemctl daemon-reload
```
```
sudo systemctl enable --now pidrum
```
```
journalctl -u pidrum -f
```

Si l'écran reste noir avec le service actif, la console occupe peut-être l'affichage :

```
sudo systemctl disable --now getty@tty1
```

## Réglages

Tout est en haut de `pidrum.py` : `BPM`, `BEAT_WINDOW` (tolérance), `BEAT_GOAL`, `KICK_IN_GAMES` (inclure la pédale dans les jeux, désactivé par défaut pour un enfant de 3 ans), `SIMON_MAX_LEN`, `HOLD_TO_SWITCH`, ainsi que les délais et seuils de *Copie-moi* (`COPY_*`).

## Pistes pour la suite

- Vélocité : volume selon la force de frappe, une fois les axes connus
- Vrais échantillons .wav à la place des sons synthétisés
- Mode « chanson » : accompagnement musical simple sur lequel taper le beat
- Extinction propre par appui long SELECT + START
