# RecoLove Modding Toolkit

Edit the 3D models and textures of **RecoLove** (PS Vita) and play your changes
on a real Vita. One window, no external tools.

- **Models (.fed)**: full support - every mesh, the **skeleton**, **per-vertex
  bone weights**, morphs (hand poses, facial expressions), materials. Free
  topology editing in Blender; unedited files stay byte-identical.
- **Textures (.tex)**: native GXT/DXT5 decoder/encoder (pixel-identical to
  GXTConvert), and the correct texture of every material is read from the model.
- **Game archives (.cpk)**: read straight from the game (CRILAYLA) and rebuilt
  with your changes - no CriPakTools needed.
- **Dead or Alive 5 LR bodies (.TMC)**: automatic body swap (fits the body to the
  RecoLove skeleton and converts the weights).
- **Blender add-on** (4.2+): import/export with skeleton, weights, morphs and
  textures; "Save to project"; "Hide in game"; use any mesh for a slot.

## Quick start (release .exe)

1. Run `RecoLoveToolkit.exe` and select your **decrypted** game folder (the one
   with `media/cpk/CharaModel.cpk`).
2. Pick a model -> **Open in Blender** / **Export textures (PNG)** /
   **Replace body with DOA5 model**.
3. **Build mod (.cpk)** and copy the files to `ux0:rePatch/PCSG00782/media/cpk/`.

Everything is explained in the app's **Help** button.

## From source

Python 3.9+ with `numpy`:

```
python recolove_toolkit.py            # GUI
python recolove_toolkit.py --help     # command line
python build_addon.py                 # Blender add-on zip
python build_release.py               # single-file .exe (needs PyInstaller + Pillow)
```

| module | what it does |
|---|---|
| `fpac.py` | generic FPAC container tree (byte-identical round trip) |
| `fedmodel.py` | .fed decode/encode (format notes in its docstring) |
| `gxt.py`, `texfile.py` | GXT DXT5 codec + Vita swizzle, .tex slots, PNG export/import |
| `cpkfile.py` | CRI .cpk reader (ITOC, CRILAYLA) and rebuilder |
| `workspace.py` | game browser, per-model projects, mod builder |
| `tmc.py`, `doa_swap.py` | DOA5LR .TMC reader and body swap |
| `glb.py`, `quickrender.py` | glTF export, preview renderer |
| `app.py`, `cli.py`, `helptext.py` | GUI, command line, user guide |
| `blender_addon/io_recolove` | Blender add-on |

## Format notes (short)

- `.fed` = FPAC tree: FPACMAT (materials), FPACTRAN (bones: parent, T, R(XYZ,
  R=Rz·Ry·Rx), S, R2), FPACGEOM (FPACMESH per slot), FPACDRMS (slot -> node),
  FPACMOR (morphs). FPACMESH block 6 = weights per unique position, block 7 =
  display vertex -> unique ids; without block 7 the vertices are interleaved.
  Rigid meshes are stored in their node's local space.
- Material texture = slot `texA` of the .tex, or `texB` if that slot is empty.
- All game textures are swizzled UBC3 (DXT5) GXT with 1 mip.

No game data is included. Use it only with games you own.
