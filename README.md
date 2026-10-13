# Mocap

**2 clicks from a video to an animated Three.js character.**

![showcase](media/showcase.gif)

Double-click `Mocap.bat`, drop a video, trim it, then:

1. **Run**: GVHMR reads the motion and retargets it onto your character (Mixamo FBX, or any GLB with `mixamorig:` bones).
2. **Export FBX**: skeleton + animation, no mesh, like a Mixamo "Without Skin" download.

Best footage: locked camera, one person, full body in frame.

## Three.js

```js
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';

const clip = (await new FBXLoader().loadAsync('clip.fbx')).animations[0];
clip.tracks = clip.tracks.filter((t) => !t.name.startsWith('Armature.'));  // bones only
clip.tracks.find((t) => t.name === 'mixamorigHips.position')
  .values.forEach((v, i, a) => (a[i] = v / 100));                         // cm -> m
new THREE.AnimationMixer(character).clipAction(clip).play();               // character: your GLB
```

## Deploy with an agent

Paste into Claude Code (or any coding agent), in an empty folder:

> Clone https://github.com/alchymi/mocap here and set it up so `Mocap.bat` works, following
> `SETUP_LOCAL.md` and `docs/INSTALL.md`: GVHMR + checkpoints in `tools/GVHMR` (Python 3.10 venv),
> a `tools/venv` with mediapipe, opencv-python, numpy, scipy, av and pillow, and Blender 5.1+.
> Ask me for the two licensed files, `SMPLX_NEUTRAL.npz` (free registration at smpl-x.is.tue.mpg.de)
> and my character; never fetch them from mirrors. Test on `tools/GVHMR/docs/example_video/tennis.mp4`.

Needs Windows, an NVIDIA GPU (~8 GB VRAM) and Blender 5.1+.

![two performers](media/showcase_duel.gif)

*Two performers in one video: supported by the underlying pipeline (command line, see [docs/](docs/)).*

Based on [squall01337/mixamo-llm-mocap](https://github.com/squall01337/mixamo-llm-mocap) (MIT).
