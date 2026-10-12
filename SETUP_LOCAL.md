# Installation locale (machine de test)

Installé le 2026-10-12, chemin **SMPL-X** (headless, pas besoin de l'add-on Blender MCP).

| Élément | Emplacement |
|---|---|
| Blender 5.2 LTS | `D:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (le dépôt exige 5.1+) |
| GVHMR | `tools\GVHMR`, venv Python 3.10 dans `tools\GVHMR\.venv` (CUDA, RTX 3090) |
| Checkpoints GVHMR (5,3 Go) | `tools\GVHMR\inputs\checkpoints\` |
| SMPL-X v1.1 | `tools\GVHMR\inputs\checkpoints\body_models\smplx\SMPLX_NEUTRAL.npz` |
| MediaPipe / OpenCV / scipy / av | `tools\venv` (Python 3.12) |
| Plate de test | `plates\tennis\tennis.mp4` (vidéo d'exemple de GVHMR, 30 fps) + `action_specs\tennis.json` |
| Personnage | `Benjamin.glb` → `benjamin_rest.blend` + `rig_profiles\benjamin.json` |

Correctif appliqué : `omegaconf==2.3.0` épinglé dans le venv GVHMR (la 2.4.0 tirée par défaut
casse `hydra-zen` : `module 'omegaconf._utils' has no attribute 'get_yaml_loader'`).

## Personnage GLB (`pipeline\setup_rig_glb.py`, ajouté localement)

Le dépôt n'accepte que des FBX Mixamo. Le script convertit un GLB dont le squelette utilise les
noms `mixamorig:` :

- garde l'armature Mixamo et ses maillages, supprime le reste (rig FaceIt, objets d'aide,
  action de test des shape keys) ;
- remet la convention d'un import FBX Mixamo (objet `Armature`, rotation X 90°, échelle 0,01) ;
- met le personnage à l'échelle pour que ses hanches soient à 0,998 m comme le Y Bot (Benjamin :
  ×2,23, il fait donc 2,23 m dans la scène). Les seuils du pipeline sont en mètres et réglés sur
  des rigs humains. Pour revenir à la taille d'origine (1 m), mettre l'échelle de l'objet
  `Armature` à 0,00448 : l'animation suit proportionnellement. `--hip-height 0` désactive la
  mise à l'échelle ;
- réoriente les phalanges (axe Z local côté paume) : le poing et la flexion des doigts sont des
  rotations autour de l'axe X local, dans la convention Mixamo que l'auto-rig de Tripo ne
  respecte pas.

## Interface (`Mocap.bat`)

Double-cliquer `Mocap.bat` : ouvre http://127.0.0.1:8765/ dans le navigateur (fermer la fenêtre
noire arrête le serveur et un traitement en cours).

1. Déposer une vidéo (ou cliquer pour la choisir) ; elle est envoyée au serveur en fond.
2. Régler entrée / sortie : poignées orange sur la timeline, ou `I` / `O` à la position
   courante. `Espace` lecture, `←` `→` image par image (`Maj` : 1 s), `L` lecture en boucle de
   la sélection.
3. Choisir le personnage (liste des .glb/.fbx à la racine), nommer le clip, « Lancer la mocap ».
4. Suivi des étapes en direct, journal détaillé en bas. À la fin : l'extrait source, boutons
   « Ouvrir dans Blender » / « Ouvrir le dossier ». Les clips précédents sont en bas de page.

Fichiers : `ui/server.py` (serveur local, bibliothèque standard) et `ui/index.html`. Les vidéos
déposées vont dans `plates\_uploads\`. Les formats que le navigateur ne lit pas (HEVC d'iPhone…)
sont prévisualisés via un proxy H.264 généré par ffmpeg.

## Export FBX « à la Mixamo » (`pipeline/mocap_fbx_export.py`)

Squelette + animation, sans mesh, comme un téléchargement Mixamo « FBX Binary, Without Skin » :
armature `Armature` (rotation X 90°, échelle 0,01 à l'import), 65 os `mixamorig:` sans os
terminaux ajoutés, une image clé par image, prise nommée comme le clip et démarrant à 0.
Fichier : `clips\<clip>\<clip>.fbx`.

- **Dans Blender** : vue 3D → barre latérale (`N`) → onglet **Mocap** → **Exporter le FBX**.
  Le script est intégré à chaque .blend généré (texte `mocap_fbx_export.py`, « Register »
  coché). Ouvert depuis l'interface, Blender autorise son exécution ; ouvert par double-clic,
  cliquer **Allow Execution** dans le bandeau d'avertissement (ou Text Editor → Run Script).
- **Dans l'interface web** : bouton **Exporter le FBX** sur l'écran de résultat.
- **En ligne de commande** :
  `blender -b clips\<clip>\<clip>.blend -P pipeline\mocap_fbx_export.py -- --export`

Par défaut l'export est à la **taille d'origine** du personnage (Benjamin : ×0,448, il refait
1 m). Les os et la translation des hanches sont mis à l'échelle, l'armature garde 0,01 comme un
fichier Mixamo. Case « Taille d'origine » décochée (ou `--pipeline-size`) : échelle de travail
du pipeline. Vérifié par réimport : 0,00 mm d'écart avec le .blend aux deux tailles.

## Lancer en ligne de commande

La logique est dans `mocap.py` (partagée avec l'interface) ; `run_test.ps1` lui passe les options.

```
powershell -ExecutionPolicy Bypass -File run_test.ps1 -Video "C:\...\ma_video.mp4"                # ta vidéo
powershell -ExecutionPolicy Bypass -File run_test.ps1 -Video "C:\...\ma_video.mp4" -Start 2 -End 12
powershell -ExecutionPolicy Bypass -File run_test.ps1 -Name ma_video        # relancer sans réimporter
powershell -ExecutionPolicy Bypass -File run_test.ps1                       # Benjamin sur "tennis"
powershell -ExecutionPolicy Bypass -File run_test.ps1 -Character ybot.fbx   # personnage Mixamo FBX
```

`-Video` copie la vidéo dans `plates\<nom>\<nom>.mp4` (nom = nom du fichier en minuscules, ou
`-Name`) : découpe `-Start`/`-End` en secondes, 30 fps max, 1920 px max, cadence fixe. Il crée
`action_specs\<nom>.json` s'il n'existe pas. Réimporter le même nom efface ses caches (ceux de la
plate et `tools\GVHMR\outputs\demo\<nom>`).

Étapes : conversion du personnage (une fois, `-Rebuild` pour refaire) → import de la vidéo →
GVHMR (sauté si `smplx.npz` existe déjà) → retarget → clés d'animation dans Blender. Résultat dans
`clips\<personnage>_<plate>\` : `<clip>.blend` (action du même nom que la plate), `motion.npz` et
`mocap.json` (plate, personnage, entrée/sortie).

`-Review` (`--review` pour `mocap.py`) ajoute les vidéos de contrôle 2×2 (`review_front.mp4`,
`review_details.mp4`, ~1 à 5 min de rendu) et les mesures (`fidelity.json`) ; l'interface les
affiche en onglets si elles existent.

Vidéo source : caméra fixe (trépied), un seul acteur, corps entier visible tout le temps (pieds
compris), membres jamais cachés derrière le corps, 720p minimum, 24–30 fps, 5–20 s.
Règles détaillées : `docs/PIPELINE.md` section 0 et `docs/PROMPTING.md` (vidéo générée par IA).
