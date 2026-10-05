"""In-app help (shown by the Help window of the GUI, one tab per entry)."""

APP_NAME = "RecoLove Modding Toolkit"
VERSION = "1.0"

TABS = [
("Quick start", """\
WELCOME!

This tool lets you change the 3D models and textures of RecoLove (PS Vita)
and play with your changes on a real Vita.

What you need
  - The DECRYPTED game files of RecoLove (folder with 'media/cpk' inside,
    e.g. PCSG00782). See the "Game files" tab if you do not have them.
  - Blender 4.2 or newer (free, blender.org) to edit models.
  - Any image editor (Photoshop, GIMP, Krita...) to edit textures.
  - A hacked PS Vita with the rePatch reDux0 plugin to play the mod.

The 4 steps
  1. PICK a model in the list on the left (search by character, costume...).
     The preview shows it with its real textures.
  2. EDIT it:
       - "Open in Blender" opens the full model (skeleton, weights, morphs,
         textures). Edit and press "Save to project" in Blender's RecoLove
         panel (sidebar, N key). The preview here updates by itself.
       - "Export textures (PNG)" puts the textures in a folder; edit them and
         press "Import edited textures".
       - "Replace body with DOA5 model" swaps the body for a Dead or Alive 5
         body (.TMC file) automatically.
  3. BUILD the mod: "Build mod (.cpk)". It packs EVERY model you edited.
  4. PLAY: copy the .cpk files to ux0:rePatch/PCSG00782/media/cpk/ on the
     Vita (details in the "Install on Vita" tab).

Your work is never lost: every edited model has its own project folder and
"Revert to original" brings the game version back at any time. Edited models
are marked with * in the list.
"""),

("Game files", """\
GETTING THE GAME FILES

The toolkit reads the game straight from its .cpk archives - nothing needs
to be extracted by hand. You only need the DECRYPTED game folder:

  <game folder>/
      media/cpk/CharaModel.cpk   (3D models)
      media/cpk/CharaTex.cpk     (textures)
      media/afs/CharaModel.als   (real file names)
      media/afs/CharaTex.als

How to get it (from YOUR copy of the game):
  - Dump the game with your Vita (e.g. with NoNpDrm / psvpfstools / VitaShell
    for cartridges) and decrypt it. The usual result is a folder named after
    the game ID (e.g. PCSG00782 or PCSG00782_dec).
  - Copy that folder to your PC and pick it with "Change..." at the top of
    the window (you can also pick its 'media' or 'media/cpk' folder).

If the toolkit says 'CharaModel.cpk not found', you picked the wrong folder
or the files are still encrypted (encrypted .cpk files cannot be read).
"""),

("Blender", """\
EDITING MODELS IN BLENDER

Click "Open in Blender". The first time, the toolkit finds Blender by itself
(or set blender.exe with "Settings"). Blender opens with the model imported.
You do not need to install anything for this.

Optional: "Install Blender add-on" installs the add-on permanently, so you
also get File > Import/Export > RecoLove Model (.fed) in any Blender session.

In Blender, open the sidebar (N key) > RecoLove tab:
  Save to project ......... writes your changes back to the toolkit (the
                            preview updates automatically). Textures you
                            painted in Blender are saved too.
  Hide in game ............ removes the selected mesh from the game (e.g. a
                            piece of clothing). Applied when you save.
  Use selected object
  for slot... ............. uses ANY mesh (imported from OBJ/FBX/another
                            game) in place of an existing slot.
  Help .................... short guide inside Blender.

What you can do
  - Move/sculpt/add/delete geometry: the topology is free.
  - Weights are the vertex groups named bone_NNN. New vertices without any
    weight automatically get the weights of the nearest original vertex.
    Max 8 influences per vertex.
  - Shape keys named morph_slotNN are game morphs (hand poses, expressions).
  - Each mesh object is one 'slot' of the file (the name ends with _sNN).

Rules
  - Apply your modifiers (except Armature) before saving.
  - Don't rename the armature or the bones.
  - Heads (Head type) are separate models from bodies (Body type).
  - Hands are separate rigid meshes (they hold the hand poses).
"""),

("Textures", """\
EDITING TEXTURES

1. Select the model and click "Export textures (PNG)". A folder opens with
   tex_00.png, tex_01.png... (the number is the texture slot used by the
   game materials).
2. Edit them in your image editor and SAVE AS PNG with the same name.
   - Keep the transparency (alpha) where it exists (hair, eyelashes).
   - You may change the size, but width and height must be powers of 2
     (256, 512, 1024...). Bigger textures use more of the Vita's memory.
3. Click "Import edited textures". Only the images you changed are
   re-encoded (the game format, DXT5, is lossy).

You can also paint directly in Blender (Texture Paint) and press
"Save to project".
"""),

("DOA5 body swap", """\
REPLACING THE BODY WITH A DEAD OR ALIVE 5 BODY

This automates the famous forum mod ("Imported Honoka body from DOAHDM").

You need the .TMC and .TMCL files of a DOA5 Last Round (PC) body - for
example a body mod (keep both files in the same folder).

1. Select a BODY model in the list (Part = Body).
2. Click "Replace body with DOA5 model..." and pick the .TMC file.
3. The dialog suggests everything automatically:
     - Skin slot: the mesh that will receive the new body.
     - Hide: the clothes to remove (click a row to see that mesh).
     - Material: a texture slot that is free to receive the new skin
       texture. If none is free, a new material is added (experimental).
     - Hands: "Use the DOA hands" (default) keeps the DOA hands - seamless
       wrists, but the fingers stay still (the game's hand poses, like a
       fist, only exist on the RecoLove hands). They are stored in one of the
       hidden clothing meshes, because a single mesh above ~8192 vertices
       crashes the game. Untick it to keep the RecoLove hands instead.
   Click "Replace body".

What it does: fits the DOA body onto the RecoLove skeleton (scale + each
arm/leg segment aligned to the RecoLove joints), converts the bone weights
and matches the skin tone to the head.

Check the result in the preview / Blender (pose the bones to test), then
build the mod. You can always "Revert to original".
"""),

("Install on Vita", """\
PLAYING YOUR MOD ON THE PS VITA

Requirements: HENkaku/Enso and the rePatch reDux0 plugin.

1. Click "Build mod (.cpk)". The output folder opens with CharaModel.cpk
   and/or CharaTex.cpk (only the archives you changed).
2. Copy them to the Vita:
       ux0:rePatch/PCSG00782/media/cpk/
   Create the folders if needed. If your game has another ID, use the name
   of the game folder in ux0:app/ instead of PCSG00782.
3. Start the game.

To uninstall, delete those .cpk files from ux0:rePatch/.../media/cpk/.

Every build contains ALL the models you edited, so you can build once with
many changes. Edited models are marked with * in the list.
"""),

("Troubleshooting", """\
TROUBLESHOOTING

'CharaModel.cpk not found'
  Pick the decrypted game folder (the one with 'media' inside).

Blender does not open / 'Blender not found'
  Install Blender 4.2+ or set the path to blender.exe in Settings.

The preview did not update after saving in Blender
  Click the model in the list again, or "Refresh".

The game freezes / crashes with my mod
  - Revert the last change and build again to find the culprit.
  - Very heavy meshes or huge textures can exceed the Vita's memory.
  - One forum user reported endless loading after hiding meshes; if that
    happens, try keeping the meshes (or hide fewer of them).

Something looks inside-out or has holes
  Recalculate the normals in Blender (Mesh > Normals > Recalculate Outside)
  for the faces you created, then save again.

A texture looks transparent
  The game uses the alpha channel as transparency: make it opaque (255).

Command line
  The .exe also works from a terminal: RecoLoveToolkit.exe --help
"""),

("About", """\
RECOLOVE MODDING TOOLKIT

Open tooling for RecoLove (PS Vita): models (.fed), textures (.tex/GXT),
game archives (.cpk) and Dead or Alive 5 bodies (.TMC).

Everything is read and written natively (no external tools):
  - .fed: full decoding/encoding - skeleton, per-vertex bone weights,
    morphs, materials, both vertex layouts; unedited files stay
    byte-identical.
  - .tex: GXT DXT5 + Vita swizzle encoder/decoder.
  - .cpk: CRI archive reader (CRILAYLA) and rebuilder.

Credits: the RecoLove modding thread on LoversLab (AlexWoods, arc037461,
lisomn, DOAX3GLITCHER and others) for the first notes about the format;
the Noesis scripts for RecoLove and DOA5LR.

This tool does not include any game data. Use it only with games you own.
"""),
]
