"""
Replaces the body (skin) of a RecoLove model with a Dead or Alive 5 LR body
(.TMC/.TMCL) - the same mod shown in the original forum thread ("Imported
Honoka body from DOAHDM"), now automatic.

How it works:
  1. Reads the TMC object (e.g. WGT_body) with weights and bind skeleton (tmc.py).
  2. FITS it onto the RecoLove skeleton: global scale s (median of limb/torso
     segment length ratios) + each arm/leg segment rotated and anchored at
     the matching RecoLove joint; the result is blended with the DOA body's
     own weights (skinning), so joints deform smoothly. The neck is anchored
     at the RecoLove neck so it meets the head (a separate file).
  3. Weights: each DOA node becomes the equivalent RecoLove bone
     (DOA_TO_RECO, standard skeleton of the 'mdb' body models).
  4. Hands: by default the DOA hands are removed and the RecoLove hands are
     kept (their poses are animated with morphs). With doa_hands=True the
     DOA hands are kept instead (seamless wrists, static fingers) and the
     RecoLove hand meshes are hidden.
  5. Writes into the chosen skin slot using a material whose texture is
     replaced with the DOA one; hides the clothing slots (zeroed indices).

Usage:
  python doa_swap.py <fed> <tex> <TMC> <out_dir> [--slot N] [--material N]
                     [--hide 1,2,3] [--tex-size 1024] [--doa-hands]
  (without --slot/--material/--hide the values are suggested automatically)
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from . import fedmodel as fm
    from . import texfile as tf
    from . import tmc as tmcmod
except ImportError:
    import fedmodel as fm
    import texfile as tf
    import tmc as tmcmod

# DOA node -> canonical RecoLove body bone (see find_body_bones; +x = the
# character's left in both games)
DOA_TO_RECO = {
    "MOT00_Hips": "pelvis", "MOT16_Waist": "spine", "MOT02_Chest": "chest", "MOT15_Neck": "neck",
    "MOT01_Head": "head",
    "MOT17_LeftShoulder": "clavicle_L", "MOT08_LeftArm": "upperarm_L", "MOT04_LeftForeArm": "forearm_L",
    "MOT05_LeftHand": "hand_L",
    "MOT19_RightShoulder": "clavicle_R", "MOT14_RightArm": "upperarm_R", "MOT10_RightForeArm": "forearm_R",
    "MOT11_RightHand": "hand_R",
    "MOT06_LeftUpLeg": "thigh_L", "MOT07_LeftLeg": "knee_L", "MOT03_LeftFoot": "ankle_L", "MOT18_LeftToe": "toe_L",
    "MOT12_RightUpLeg": "thigh_R", "MOT13_RightLeg": "knee_R", "MOT09_RightFoot": "ankle_R", "MOT20_RightToe": "toe_R",
    "OPT_AS_L_Wrist_A": "forearm_twist_L", "OPT_AS_L_Wrist_B": "forearm_twist_L",
    "OPT_AS_R_Wrist_A": "forearm_twist_R", "OPT_AS_R_Wrist_B": "forearm_twist_R",
    "OPT_Breast_Left_base": "breast_L", "OPT_Breast_Right_base": "breast_R",
}
# name prefixes mapped to a specific bone
DOA_PREFIX = [("OPT_Breast_Left", "breast_tip_L"), ("OPT_Breast_Right", "breast_tip_R")]

# segments with rotation+anchoring: DOA node -> (DOA child, RecoLove bone, RecoLove child bone)
SEGMENTS = {
    "MOT08_LeftArm": ("MOT04_LeftForeArm", "upperarm_L", "forearm_L"),
    "MOT04_LeftForeArm": ("MOT05_LeftHand", "forearm_L", "hand_L"),
    "MOT05_LeftHand": ("MOT04_LeftForeArm", "hand_L", "forearm_L"),
    "MOT14_RightArm": ("MOT10_RightForeArm", "upperarm_R", "forearm_R"),
    "MOT10_RightForeArm": ("MOT11_RightHand", "forearm_R", "hand_R"),
    "MOT11_RightHand": ("MOT10_RightForeArm", "hand_R", "forearm_R"),
    "MOT06_LeftUpLeg": ("MOT07_LeftLeg", "thigh_L", "knee_L"),
    "MOT07_LeftLeg": ("MOT03_LeftFoot", "knee_L", "ankle_L"),
    "MOT03_LeftFoot": ("MOT18_LeftToe", "ankle_L", "toe_L"),
    "MOT18_LeftToe": ("MOT03_LeftFoot", "toe_L", "ankle_L"),
    "MOT12_RightUpLeg": ("MOT13_RightLeg", "thigh_R", "knee_R"),
    "MOT13_RightLeg": ("MOT09_RightFoot", "knee_R", "ankle_R"),
    "MOT09_RightFoot": ("MOT20_RightToe", "ankle_R", "toe_R"),
    "MOT20_RightToe": ("MOT09_RightFoot", "toe_R", "ankle_R"),
}
ANCHOR_ONLY = {"MOT15_Neck": "neck", "MOT01_Head": "head"}  # translation only (no rotation)
HAND_PREFIXES = ("MOT05_LeftHand", "MOT11_RightHand", "OPT_Hand_")


class SwapError(Exception):
    pass


def find_body_bones(model):
    """Identifies the standard body bones by STRUCTURE (the index order
    changes from file to file). Returns {canonical_name: bone_index}."""
    W = model.world_matrices()
    n = len(model.bones)
    pos = [(W[i][0][3], W[i][1][3], W[i][2][3]) for i in range(n)]
    kids = {i: [] for i in range(n)}
    for b in model.bones:
        if 0 <= b.parent < n and b.parent != b.index:
            kids[b.parent].append(b.index)

    def depth_min_y(i, d=0):
        ys = [pos[i][1]]
        if d < 64:
            for k in kids[i]:
                ys.append(depth_min_y(k, d + 1))
        return min(ys)

    def has_kids(i):
        return bool(kids[i])

    roots = [b.index for b in model.bones if b.parent < 0 and pos[b.index][1] > 1.0 and len(kids[b.index]) >= 2]
    if not roots:
        raise ValueError("no hips/root bone found")
    root = max(roots, key=lambda r: len(kids[r]))
    up = [k for k in kids[root] if pos[k][1] > pos[root][1]]
    down = [k for k in kids[root] if pos[k][1] < pos[root][1]]
    spine = max(up, key=lambda k: len(kids[k]))
    pelvis = min(down, key=lambda k: depth_min_y(k))
    chest = max(kids[spine], key=lambda k: len(kids[k]))
    cy = pos[chest][1]
    out = dict(root=root, spine=spine, chest=chest, pelvis=pelvis)
    center_high = [k for k in kids[chest] if abs(pos[k][0]) < 0.02 and pos[k][1] > cy + 0.5]
    neck = max(center_high, key=lambda k: len(kids[k]) * 10 - pos[k][2])
    out["neck"] = neck
    out["head"] = max(kids[neck], key=lambda k: pos[k][1])
    for side, sgn in (("L", 1), ("R", -1)):
        clav = [k for k in kids[chest] if sgn * pos[k][0] > 0.03 and pos[k][1] > cy + 0.5]
        clav = max(clav, key=lambda k: len(kids[k]))
        arm = max([k for k in kids[clav] if has_kids(k)], key=lambda k: sgn * pos[k][0])
        fore = max([k for k in kids[arm] if has_kids(k)], key=lambda k: sgn * pos[k][0])
        fk = sorted(kids[fore], key=lambda k: -sgn * pos[k][0])
        hand = fk[0]
        twist = fk[1] if len(fk) > 1 else fore
        breast = [k for k in kids[chest] if sgn * pos[k][0] > 0.1 and pos[k][1] < cy + 0.5
                  and pos[k][2] > pos[chest][2] + 0.15
                  and kids[k] and pos[kids[k][0]][2] > pos[k][2]]  # tip points forward
        out.update({"clavicle_" + side: clav, "upperarm_" + side: arm, "forearm_" + side: fore,
                    "hand_" + side: hand, "forearm_twist_" + side: twist})
        if breast:
            b = max(breast, key=lambda k: pos[k][2])
            out["breast_" + side] = b
            out["breast_tip_" + side] = kids[b][0] if kids[b] else b
        else:
            out["breast_" + side] = out["breast_tip_" + side] = chest
        legs = [k for k in kids[pelvis] if sgn * pos[k][0] > 0.1]
        thigh = min(legs, key=lambda k: depth_min_y(k))
        knee = min(kids[thigh], key=lambda k: depth_min_y(k))
        ankle = min(kids[knee], key=lambda k: depth_min_y(k))
        toe = max(kids[ankle], key=lambda k: pos[k][2]) if kids[ankle] else ankle
        out.update({"thigh_" + side: thigh, "knee_" + side: knee, "ankle_" + side: ankle, "toe_" + side: toe})
    return out


def check_body_model(model):
    """Returns the canonical bone map, or raises SwapError for non-body models."""
    try:
        return find_body_bones(model)
    except (ValueError, KeyError, IndexError) as e:
        raise SwapError("This model does not have the standard full-body skeleton (%s). "
                        "Pick a BODY model (type 'Body', files named mdb_...)." % e)


def hide_any_slot(model, index):
    """Hides a slot by zeroing its triangle indices (Model.hide_slot - the
    method tested on the Vita, hands included)."""
    if not model.meshes[index].is_hidden:
        model.hide_slot(index)


# Biggest mesh known to load on the Vita: 7362 unique / 8115 drawn vertices.
# 8570 / 9785 crashed the game (vita test T2) - the limit is most likely 8192.
VERTEX_LIMIT = 8192


def check_vertex_limit(slot, what):
    drawn = max(len(sm.positions) for sm in slot.submeshes)
    uniq = len(slot.uniq_pos)
    if drawn >= VERTEX_LIMIT or uniq >= VERTEX_LIMIT:
        raise SwapError("The new %s mesh has %d vertices (%d drawn) - the game crashes above ~%d. "
                        "Use a lighter model." % (what, uniq, drawn, VERTEX_LIMIT))


def minimize_hidden_slot(model, index):
    """Replaces a HIDDEN slot by a tiny triangle (indices zeroed afterwards,
    like hide_slot) to save memory - keeps the total model size at or below
    the size that is known to load. Morph bases are never touched."""
    s = model.meshes[index]
    if s.morph_targets or s.kind != fm.MeshSlot.KIND_GEOMETRY or not s.uniq_pos:
        return
    p = s.uniq_pos[0]
    pts = [p, (p[0] + 1e-3, p[1], p[2]), (p[0], p[1] + 1e-3, p[2])]
    w = [list(s.vertex_weights(0))] * 3 if s.is_skinned else None
    sm0 = s.submeshes[0]
    n = sm0.normals[0] if sm0.normals else None
    uv = sm0.uvs[0] if sm0.uvs else None
    s.set_geometry(pts, [(255, 255, 255, 255)] * 3, w,
                   [dict(material_id=sm0.material_id, flags=sm0.flags,
                         triangles=[((0, n, uv), (1, n, uv), (2, n, uv))])])
    model.hide_slot(index)


def list_objects(tmc_path):
    """Names of the mesh objects inside a TMC, biggest first."""
    T = tmcmod.Tmc(tmc_path)
    objs = sorted(T.objects, key=lambda o: -len(o.positions))
    return [(o.name, len(o.positions)) for o in objs]


def hand_slots(model, bones=None):
    """Rigid RecoLove hand meshes: slots whose node hangs below a hand bone."""
    bones = bones or check_body_model(model)
    hands = {bones["hand_L"], bones["hand_R"]}
    out = []
    for s in model.geometry_slots():
        if s.is_skinned or s.is_hidden or s.bone is None:
            continue
        j, depth = s.bone, 0
        while 0 <= j < len(model.bones) and depth < 64:
            if j in hands:
                out.append(s.index)
                break
            j = model.bones[j].parent
            depth += 1
    return out


def suggest_params(model, tex, doa_hands=False):
    """Best guess for (skin_slot, material, hide_slots, skin_tone_ref_slot).
    skin = the skinned slot covering the biggest height (legs + torso);
    hide = every other skinned slot (clothes); the RecoLove hands (rigid)
    are kept - or hidden too when doa_hands=True (the DOA hands are used);
    material = a textured material whose texture is only used by hidden
    slots (so replacing that texture breaks nothing visible)."""
    bones = check_body_model(model)
    skinned = [s for s in model.geometry_slots() if s.is_skinned and not s.is_hidden]
    if not skinned:
        raise SwapError("No skinned mesh found in this model.")

    def height(s):
        ys = [p[1] for p in s.uniq_pos]
        return max(ys) - min(ys)

    skin = max(skinned, key=height)
    hide = [s.index for s in skinned if s.index != skin.index]
    if doa_hands:
        hide += hand_slots(model, bones)
    visible =[s for s in model.geometry_slots() if s.index not in hide and s.index != skin.index
               and not s.is_hidden]
    used_visible = set()
    for s in visible:
        for mid in s.material_ids:
            if mid < len(model.materials):
                used_visible.add(model.materials[mid].texture_slot(tex))
    material = None
    for m in model.materials:
        if not m.textured:
            continue
        ts = m.texture_slot(tex)
        if ts is not None and ts not in used_visible:
            material = m.index
            break
    rigid = [s for s in model.geometry_slots() if not s.is_skinned and not s.is_hidden]
    ref = rigid[0].index if rigid else None
    return skin.index, material, hide, ref


def _rot_between(a, b):
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else -np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


class Retarget(object):
    def __init__(self, T, model):
        self.T = T
        self.bones = bones = check_body_model(model)
        W = model.world_matrices()
        self.R = lambda name: np.array([W[bones[name]][k][3] for k in range(3)])
        names = T.node_names
        for n in list(SEGMENTS) + list(ANCHOR_ONLY) + ["MOT00_Hips"]:
            if n not in names:
                raise SwapError("The TMC skeleton has no node '%s' - not a standard DOA5 body?" % n)
        P = lambda n: T.bind_position(T.node_index(n))
        # global scale: median of the segment length ratios
        ratios = []
        for n, (child, rb, rc) in SEGMENTS.items():
            if n in ("MOT05_LeftHand", "MOT11_RightHand", "MOT18_LeftToe", "MOT20_RightToe"):
                continue
            lh = np.linalg.norm(P(child) - P(n))
            lr = np.linalg.norm(self.R(rc) - self.R(rb))
            if lh > 1e-6:
                ratios.append(lr / lh)
        lh = np.linalg.norm(P("MOT15_Neck") - P("MOT00_Hips"))
        lr = np.linalg.norm(self.R("neck") - self.R("pelvis"))
        ratios.append(lr / lh)
        self.s = float(np.median(ratios))
        # global anchor: midpoint between the hip joints
        hmid = (P("MOT06_LeftUpLeg") + P("MOT12_RightUpLeg")) / 2
        rmid = (self.R("thigh_L") + self.R("thigh_R")) / 2
        self.global_t = rmid - self.s * hmid

        # (3x3 A, t) per node: v' = A v + t
        self.mats = {}
        for i in range(len(names)):
            self.mats[i] = (self.s * np.eye(3), self.global_t)
        for n, (child, rb, rc) in SEGMENTS.items():
            dh = P(child) - P(n)
            dr = self.R(rc) - self.R(rb)
            A = self.s * _rot_between(dh, dr)
            self.mats[T.node_index(n)] = (A, self.R(rb) - A @ P(n))
        for n, rb in ANCHOR_ONLY.items():
            A = self.s * np.eye(3)
            self.mats[T.node_index(n)] = (A, self.R(rb) - A @ P(n))
        # helper nodes (twist, fingers, breasts...) inherit the fit of their main parent
        main = set(T.node_index(n) for n in list(SEGMENTS) + list(ANCHOR_ONLY))
        for i in range(len(names)):
            if i in main or names[i].startswith("MOT"):
                continue
            j = T.parents[i]
            while j >= 0 and j not in main and not names[j].startswith("MOT"):
                j = T.parents[j]
            if j >= 0:
                self.mats[i] = self.mats[j]

    def reco_bone(self, node):
        """RecoLove bone index for a DOA node."""
        name = self.T.node_names[node]
        if name in DOA_TO_RECO:
            return self.bones[DOA_TO_RECO[name]]
        for pre, b in DOA_PREFIX:
            if name.startswith(pre):
                return self.bones[b]
        j = self.T.parents[node]
        while j >= 0:
            if self.T.node_names[j] in DOA_TO_RECO:
                return self.bones[DOA_TO_RECO[self.T.node_names[j]]]
            j = self.T.parents[j]
        return self.bones["root"]

    def apply(self, pos, nrm, weights):
        pos = np.asarray(pos, dtype=np.float64)
        v = np.zeros(3)
        A_acc = np.zeros((3, 3))
        for node, w in weights:
            A, t = self.mats[node]
            v += w * (A @ pos + t)
            A_acc += w * A
        n = A_acc @ np.asarray(nrm, dtype=np.float64)
        n /= max(np.linalg.norm(n), 1e-12)
        return v, n


def _mean_texel(img, uvs):
    """Average color of the texels at the given UVs (u, v with origin at the top)."""
    h, w = img.shape[:2]
    uv = np.asarray(uvs, dtype=np.float64)
    x = (np.floor(uv[:, 0] * w).astype(int)) % w
    y = (np.floor(uv[:, 1] * h).astype(int)) % h
    return img[y, x, :3].astype(np.float64).mean(axis=0)


def match_skin_tone(img, ob, model, tex, ref_slot, drop, log=print):
    """Shifts the DOA texture to the average RecoLove skin tone (sampled at
    the UVs of a reference slot, e.g. the hands), so there is no color seam
    at the wrists and the neck."""
    ref = model.meshes[ref_slot]
    sm = ref.submeshes[0]
    mat = model.materials[sm.material_id]
    _w, _h, rimg = tex.decode(mat.texture_slot(tex))
    target = _mean_texel(rimg, [(u, v + 1.0) for (u, v) in sm.uvs])
    src = _mean_texel(img, [ob.uvs[i] for i in range(len(ob.uvs)) if i not in drop])
    # shift the mean to the RecoLove tone and compress the variation (the
    # anime RecoLove skin is almost flat); a plain multiply blows out highlights
    contrast = 0.6
    log("skin tone: DOA %s -> RecoLove %s" % (np.round(src).astype(int).tolist(), np.round(target).astype(int).tolist()))
    out = img.copy()
    rgb = target + (img[..., :3].astype(np.float64) - src) * contrast
    out[..., :3] = np.clip(rgb, 0, 255).round().astype(np.uint8)
    return out


def swap_body(fed_in, tex_in, tmc_path, fed_out, tex_out, slot=None, material=None, hide=None,
              tex_size=1024, object_name=None, vertex_color=(255, 252, 252, 255), match_skin=True,
              doa_hands=False, log=print):
    """Does the whole swap. Missing slot/material/hide are suggested
    automatically (suggest_params). doa_hands=True keeps the DOA hands
    (seamless wrists, but the fingers follow the hand bone only - the
    RecoLove hand poses are morphs of the RecoLove hands, which get hidden).
    Returns a dict with what was done."""
    model = fm.Model.load(fed_in)
    tex = tf.TexFile.load(tex_in)
    check_body_model(model)
    s_slot, s_mat, s_hide, ref = suggest_params(model, tex, doa_hands)
    slot = s_slot if slot is None else slot
    material = s_mat if material is None else material
    hide = s_hide if hide is None else list(hide)
    new_material = material is None or material == "new"
    T = tmcmod.Tmc(tmc_path)
    ob = T.object(object_name) if object_name else max(T.objects, key=lambda o: len(o.positions))
    if not ob.weights:
        raise SwapError("Object '%s' has no bone weights." % ob.name)
    rt = Retarget(T, model)
    log("DOA object '%s': %d vertices; scale DOA -> RecoLove: %.3f" % (ob.name, len(ob.positions), rt.s))

    names = T.node_names
    hand_v = {i for i, ws in enumerate(ob.weights)
              if sum(w for n, w in ws if names[n].startswith(HAND_PREFIXES)) >= 0.5}
    drop = set() if doa_hands else hand_v  # vertices that are not used at all

    cache = {}

    def vert(i):
        """DOA vertex -> (RecoLove position, normal, RecoLove weights)."""
        if i not in cache:
            p, n = rt.apply(ob.positions[i], ob.normals[i], ob.weights[i])
            rw = {}
            for node, w in ob.weights[i]:
                b = rt.reco_bone(node)
                rw[b] = rw.get(b, 0.0) + w
            items = sorted(((b, w) for b, w in rw.items() if w >= 0.01), key=lambda x: -x[1])[:4]
            tot = sum(w for _, w in items)
            cache[i] = (tuple(float(x) for x in p), tuple(float(x) for x in n), [(b, w / tot) for b, w in items])
        return cache[i]

    def build(src_tris):
        """DOA triangles -> (uniq_pos, uniq_weights, triangles) for set_geometry.
        Positions shared by UV seams are welded."""
        uniq_pos, uniq_w, key_of, vid_to_uid, tris = [], [], {}, {}, []
        for v in sorted({v for t in src_tris for v in t}):  # DOA vertex order (stable output)
            key = tuple(np.round(ob.positions[v], 6))
            if key not in key_of:
                key_of[key] = len(uniq_pos)
                p, _n, w = vert(v)
                uniq_pos.append(p)
                uniq_w.append(w)
            vid_to_uid[v] = key_of[key]
        for (a, b, c) in src_tris:
            pa = lambda i: np.array(uniq_pos[vid_to_uid[i]])
            g = np.cross(pa(b) - pa(a), pa(c) - pa(a))
            n = np.array(vert(a)[1]) + np.array(vert(b)[1]) + np.array(vert(c)[1])
            if np.dot(g, n) > 0:  # RecoLove stores triangles clockwise relative to the normal
                b, c = c, b
            tri = []
            for v in (a, b, c):
                u, vv = ob.uvs[v]
                tri.append((vid_to_uid[v], vert(v)[1], (float(u), float(vv) - 1.0)))
            if len({t[0] for t in tri}) == 3:
                tris.append(tuple(tri))
        return uniq_pos, uniq_w, tris

    # The body and the hands are built from the same DOA vertices: the wrist
    # vertices exist in both meshes with identical position and weights, so
    # they bend together (no crack).
    body_src = [t for t in ob.triangles if not any(v in hand_v for v in t)]
    hand_src = [t for t in ob.triangles if any(v in hand_v for v in t)]
    uniq_pos, uniq_w, tris = build(body_src)

    hand_slot = None
    if doa_hands:
        # On the Vita, a mesh above ~8192 vertices crashes the game, so the DOA
        # hands go into a SEPARATE slot: a hidden, skinned clothing slot.
        cands = [h for h in hide if h != slot and model.meshes[h].kind == fm.MeshSlot.KIND_GEOMETRY
                 and model.meshes[h].is_skinned and not model.meshes[h].morph_targets
                 and not model.meshes[h].is_hidden]
        if not cands:
            raise SwapError("No free (hidden clothing) mesh slot to hold the DOA hands. "
                            "Untick 'Use the DOA hands'.")
        hand_slot = max(cands, key=lambda h: len(model.meshes[h].uniq_pos))
        hide = [h for h in hide if h != hand_slot]

    s = model.meshes[slot]
    if new_material:
        # no free texture: add a texture slot + a material (copy of the skin one)
        tex.root.children.append(b"")
        material = model.add_material(s.submeshes[0].material_id, len(tex) - 1)
        log("no free texture slot - added material %d + texture slot %d (experimental)" % (material, len(tex) - 1))
    flags = s.submeshes[0].flags if s.submeshes else (fm.FLAG_NORMAL | fm.FLAG_COLOR | fm.FLAG_UV)
    s.set_geometry(uniq_pos, [vertex_color] * len(uniq_pos), uniq_w,
                   [dict(material_id=material, flags=flags, triangles=tris)])
    check_vertex_limit(s, "body")

    hand_info = None
    if hand_slot is not None:
        h_pos, h_w, h_tris = build(hand_src)
        hs = model.meshes[hand_slot]
        hflags = hs.submeshes[0].flags if hs.submeshes else flags
        hs.set_geometry(h_pos, [vertex_color] * len(h_pos), h_w,
                        [dict(material_id=material, flags=hflags, triangles=h_tris)])
        check_vertex_limit(hs, "hands")
        hand_info = (hand_slot, len(h_pos), len(h_tris))

    for h in hide:
        if h != slot:
            hide_any_slot(model, h)
            if doa_hands:
                minimize_hidden_slot(model, h)

    # texture: diffuse of the object's material (texture slot 0 of the list)
    diffuse = [t for t in ob.textures if t[0] == 0]
    if not diffuse:
        raise SwapError("Object '%s' has no diffuse texture." % ob.name)
    img = T.texture(diffuse[0][2]).copy()
    img[..., 3] = 255  # DOA diffuse alpha is not transparency; in RecoLove it is
    f = img.shape[0] // tex_size
    if f > 1 and img.shape[0] == img.shape[1]:
        img = img.reshape(tex_size, f, tex_size, f, 4).mean(axis=(1, 3)).round().astype(np.uint8)
    if match_skin and ref is not None:
        img = match_skin_tone(img, ob, model, tex, ref, hand_v, log)
    tslot = (len(tex) - 1) if new_material else model.materials[material].texture_slot(tex)
    tex.replace_rgba(tslot, img, img.shape[1], img.shape[0])

    model.save(fed_out)
    tex.save(tex_out)
    fm.Model.load(fed_out)  # sanity check: must open again
    info = dict(slot=slot, material=material, texture_slot=tslot, hidden=[h for h in hide if h != slot],
                vertices=len(uniq_pos), triangles=len(tris), hand_vertices_removed=len(drop),
                hand_slot=hand_info[0] if hand_info else None, scale=rt.s, object=ob.name,
                file_size=os.path.getsize(fed_out))
    log("slot %d (body) <- %d vertices, %d triangles; material %d -> texture slot %d (%dx%d)"
        % (slot, len(uniq_pos), len(tris), material, tslot, img.shape[1], img.shape[0]))
    if hand_info:
        log("slot %d (DOA hands) <- %d vertices, %d triangles" % hand_info)
    else:
        log("DOA hands removed (%d vertices) - the RecoLove hands are kept" % len(drop))
    log("hidden slots: %s; model file %d bytes" % (info["hidden"], info["file_size"]))
    return info


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fed")
    p.add_argument("tex")
    p.add_argument("tmc")
    p.add_argument("out_dir")
    p.add_argument("--slot", type=int, default=None)
    p.add_argument("--material", type=int, default=None)
    p.add_argument("--hide", default=None, help="comma separated slot list")
    p.add_argument("--tex-size", type=int, default=1024)
    p.add_argument("--object", default=None)
    p.add_argument("--doa-hands", action="store_true", help="keep the DOA hands (hides the RecoLove ones)")
    a = p.parse_args()
    hide = [int(x) for x in a.hide.split(",") if x.strip()] if a.hide is not None else None
    os.makedirs(a.out_dir, exist_ok=True)
    swap_body(a.fed, a.tex, a.tmc, os.path.join(a.out_dir, os.path.basename(a.fed)),
              os.path.join(a.out_dir, os.path.basename(a.tex)), a.slot, a.material, hide, a.tex_size, a.object,
              doa_hands=a.doa_hands)


if __name__ == "__main__":
    main()
