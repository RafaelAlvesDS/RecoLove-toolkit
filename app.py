#!/usr/bin/env python3
"""
RecoLove Modding Toolkit - graphical interface (Tkinter).

Pick a model -> edit it (Blender / PNG textures / DOA5 body swap) -> build the
mod .cpk -> copy it to the Vita. See helptext.py for the user guide.
"""
import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_launch
import doa_swap
import fedmodel
import glb
import helptext
import quickrender
import texfile
import workspace as wsmod

PREVIEW_W = 520
FILTERS = ["All models", "Bodies", "Heads", "Chibi / other", "Edited only"]


def app_dir():
    """Folder of the .exe (frozen) or WORKPLACE (source tree)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_workspace():
    name = "RecoLoveToolkit_Workspace" if getattr(sys, "frozen", False) else "toolkit_workspace"
    return os.path.join(app_dir(), name)


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    os.startfile(path)


class App(object):
    def __init__(self, root):
        self.root = root
        root.title("%s %s" % (helptext.APP_NAME, helptext.VERSION))
        root.geometry("1280x820")
        root.minsize(1050, 680)
        icon = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "toolkit.ico")
        if os.path.isfile(icon):
            try:
                root.iconbitmap(default=icon)  # also used by every dialog
            except tk.TclError:
                pass

        self.ws = wsmod.Workspace(default_workspace())
        self.game = None
        self.project = None
        self.entry = None
        self.view = "xy"
        self._photo = None
        self._busy = False
        self._events = queue.Queue()
        self._mtimes = None
        self._edited = set()
        self._rows = {}

        self._style()
        self._build()
        self.root.after(100, self._pump)
        self.root.after(1500, self._watch_project)
        self.root.after(200, self._startup)

    # ------------------------------------------------------------------ style
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("vista" if "vista" in st.theme_names() else "clam")
        except tk.TclError:
            pass
        st.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        st.configure("Sub.TLabel", foreground="#666")
        st.configure("Step.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        st.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 6))
        st.configure("TButton", padding=(8, 3))
        st.configure("Treeview", rowheight=22)
        st.configure("Info.TLabel", font=("Segoe UI", 9))

    # ------------------------------------------------------------------ layout
    def _build(self):
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text=helptext.APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Help", command=self.show_help).pack(side="right", padx=3)
        ttk.Button(top, text="Settings...", command=self.settings_dialog).pack(side="right", padx=3)
        ttk.Button(top, text="Install Blender add-on", command=self.install_addon).pack(side="right", padx=3)
        ttk.Button(top, text="Change game folder...", command=self.choose_game).pack(side="right", padx=3)
        self.var_game = tk.StringVar(value="No game folder selected")
        ttk.Label(top, textvariable=self.var_game, style="Sub.TLabel").pack(side="right", padx=12)

        main = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        main.pack(fill="both", expand=True)
        self._main = main

        # --- left: model browser
        left = ttk.Frame(main, width=330)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        ttk.Label(left, text="1. Pick a model").pack(anchor="w")
        self.var_search = tk.StringVar()
        e = ttk.Entry(left, textvariable=self.var_search)
        e.pack(fill="x", pady=(4, 2))
        e.bind("<KeyRelease>", lambda ev: self.refresh_list())
        self.var_filter = tk.StringVar(value=FILTERS[0])
        cb = ttk.Combobox(left, textvariable=self.var_filter, values=FILTERS, state="readonly")
        cb.pack(fill="x", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda ev: self.refresh_list())
        frame = ttk.Frame(left)
        frame.pack(fill="both", expand=True, pady=2)
        cols = ("character", "part", "costume")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for c, w, t in zip(cols, (120, 70, 120), ("Character", "Part", "Costume")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("edited", foreground="#c0392b", font=("Segoe UI", 9, "bold"))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.var_count = tk.StringVar()
        ttk.Label(left, textvariable=self.var_count, style="Sub.TLabel").pack(anchor="w")
        ttk.Label(left, text="Type to search. * = edited model.", style="Sub.TLabel").pack(anchor="w")

        # --- right: actions
        right = ttk.Frame(main, width=300)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        self._actions = []

        def step(title):
            f = ttk.LabelFrame(right, text=title, style="Step.TLabelframe", padding=(8, 4))
            f.pack(fill="x", pady=(0, 6))
            return f

        def button(parent, text, cmd, big=False, needs_model=True, tip=None):
            b = ttk.Button(parent, text=text, command=cmd, style="Big.TButton" if big else "TButton")
            b.pack(fill="x", pady=1)
            if needs_model:
                self._actions.append(b)
            if tip:
                ttk.Label(parent, text=tip, style="Sub.TLabel", wraplength=270, justify="left").pack(anchor="w")
            return b

        s2 = step("2. Edit")
        button(s2, "Open in Blender", self.open_blender, big=True)
        button(s2, "Export textures (PNG)", self.export_textures)
        button(s2, "Replace body with DOA5 model...", self.swap_dialog)
        s3 = step("3. Bring changes back")
        ttk.Label(s3, text="Blender: press 'Save to project' in the RecoLove panel (N key). "
                           "This window updates by itself.", style="Sub.TLabel", wraplength=270,
                  justify="left").pack(anchor="w", pady=(0, 4))
        button(s3, "Import edited textures", self.import_textures)
        button(s3, "Load a .fed file...", self.load_fed)
        s5 = step("4. Play it on the Vita")
        button(s5, "Build mod (.cpk)", self.build_mod, big=True, needs_model=False)
        button(s5, "Open mod folder", lambda: open_folder(self.ws.output_dir), needs_model=False,
               tip="Copy the .cpk files to ux0:rePatch/PCSG00782/media/cpk/ (Help > Install on Vita).")
        s4 = step("More tools")
        grid = ttk.Frame(s4)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        for i, (text, cmd) in enumerate((("Change material...", self.material_dialog),
                                         ("Export .glb", self.export_glb),
                                         ("Project folder", lambda: self.project and open_folder(self.project.folder)),
                                         ("Revert to original", self.revert))):
            b = ttk.Button(grid, text=text, command=cmd)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=1, pady=1)
            self._actions.append(b)

        # --- center: preview
        center = ttk.Frame(main, padding=(10, 0))
        center.pack(side="left", fill="both", expand=True)
        self.var_title = tk.StringVar(value="")
        ttk.Label(center, textvariable=self.var_title, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.var_info = tk.StringVar(value="")
        ttk.Label(center, textvariable=self.var_info, style="Info.TLabel", wraplength=560,
                  justify="left").pack(anchor="w")
        self.canvas = tk.Canvas(center, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=4)
        self._canvas_size = (PREVIEW_W, 600)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        views = ttk.Frame(center)
        views.pack()
        for label, axis in (("Front", "xy"), ("Back", "-xy"), ("Side", "zy"), ("Top", "xz")):
            ttk.Button(views, text=label, width=8, command=lambda a=axis: self.set_view(a)).pack(side="left", padx=2)
        ttk.Button(views, text="Refresh", command=self.reload_project).pack(side="left", padx=10)

        # welcome panel (shown when there is no game folder)
        self.welcome = ttk.Frame(center, padding=30)
        ttk.Label(self.welcome, text="Welcome!", style="Title.TLabel").pack(pady=(40, 10))
        ttk.Label(self.welcome, text="Select your DECRYPTED RecoLove game folder\n"
                                     "(the folder that contains 'media/cpk/CharaModel.cpk').",
                  justify="center").pack(pady=6)
        ttk.Button(self.welcome, text="Select game folder...", style="Big.TButton",
                   command=self.choose_game).pack(pady=10)
        ttk.Button(self.welcome, text="How do I get the game files?",
                   command=lambda: self.show_help("Game files")).pack()

        # --- bottom: status + log
        bottom = ttk.Frame(self.root, padding=(10, 2, 10, 6))
        bottom.pack(fill="x", side="bottom", before=self._main)
        self.var_status = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.var_status).pack(side="left")
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=160)
        self.btn_log = ttk.Button(bottom, text="Show log", command=self.toggle_log)
        self.btn_log.pack(side="right")
        self.log_frame = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        self.log_text = tk.Text(self.log_frame, height=9, bg="#151515", fg="#dcdcdc", font=("Consolas", 9),
                                state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self._set_actions(False)

    # ------------------------------------------------------------------ tasks / log
    def log(self, msg):
        self._events.put(("log", str(msg)))

    def _pump(self):
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", payload + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def run(self, status, fn, done=None, error_title="Error"):
        """Runs fn() in a worker thread; done(result) runs on the UI thread."""
        if self._busy:
            messagebox.showinfo("Please wait", "Another task is still running.")
            return
        self._busy = True
        self.var_status.set(status)
        self.progress.pack(side="left", padx=10)
        self.progress.start(12)
        self.log(status)

        def work():
            try:
                res = fn()
                self._events.put(("call", lambda: self._finish(done, res, None, error_title)))
            except Exception as e:
                tb = traceback.format_exc()
                self._events.put(("call", lambda: self._finish(done, None, (e, tb), error_title)))
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, done, res, err, title):
        self._busy = False
        self.progress.stop()
        self.progress.pack_forget()
        if err:
            e, tb = err
            self.log(tb)
            self.var_status.set("Error: %s" % e)
            messagebox.showerror(title, str(e))
            return
        self.var_status.set("Ready.")
        if done:
            done(res)

    def toggle_log(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.pack_forget()
            self.btn_log.configure(text="Show log")
        else:
            self.log_frame.pack(fill="x", side="bottom", before=self._main)
            self.btn_log.configure(text="Hide log")

    def _set_actions(self, enabled):
        for b in self._actions:
            b.state(["!disabled"] if enabled else ["disabled"])

    # ------------------------------------------------------------------ game folder
    def _startup(self):
        gdir = self.ws.settings.get("game_dir") or wsmod.find_game_dir(
            [app_dir(), os.path.dirname(app_dir()), os.path.join(os.path.expanduser("~"), "Desktop")])
        if gdir:
            self.set_game(gdir, quiet=True)
        else:
            self._show_welcome(True)
        if not self.ws.settings.get("help_shown"):
            self.ws.settings["help_shown"] = True
            self.ws.save_settings()
            self.show_help()

    def _show_welcome(self, show):
        if show:
            self.welcome.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.welcome.lift()
        else:
            self.welcome.place_forget()

    def choose_game(self):
        d = filedialog.askdirectory(title="Select the decrypted RecoLove game folder")
        if d:
            self.set_game(d)

    def set_game(self, gdir, quiet=False):
        try:
            game = wsmod.Game(gdir)
        except wsmod.GameError as e:
            if not quiet:
                messagebox.showerror("Game folder", str(e))
            self._show_welcome(self.game is None)
            return
        self.game = game
        self.ws.settings["game_dir"] = gdir
        self.ws.save_settings()
        self.var_game.set("Game: %s" % gdir)
        self._show_welcome(False)
        self._edited = {p.name for p in self.ws.projects() if p.is_modified()}
        self.refresh_list()
        self.log("Game folder: %s (%d models, %d texture files)" % (gdir, len(game.models), len(game.textures)))

    # ------------------------------------------------------------------ list
    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        self._rows = {}
        if not self.game:
            return
        q = self.var_search.get().strip().lower()
        f = self.var_filter.get()
        n = 0
        for e in self.game.models:
            if e.part == "Collision":
                continue
            edited = e.name in self._edited
            if f == "Bodies" and e.part != "Body":
                continue
            if f == "Heads" and e.part != "Head":
                continue
            if f == "Chibi / other" and e.part in ("Body", "Head"):
                continue
            if f == "Edited only" and not edited:
                continue
            text = " ".join((e.character, e.part, e.costume, e.basename)).lower()
            if q and not all(w in text for w in q.split()):
                continue
            iid = str(e.id)
            self.tree.insert("", "end", iid=iid, values=(("* " if edited else "") + e.character, e.part, e.costume),
                             tags=("edited",) if edited else ())
            self._rows[iid] = e
            n += 1
        self.var_count.set("%d models" % n)
        if self.entry is not None and str(self.entry.id) in self._rows:
            self.tree.selection_set(str(self.entry.id))
            self.tree.see(str(self.entry.id))

    def _mark_edited(self):
        if self.project is None:
            return
        was = self.project.name in self._edited
        now = self.project.is_modified()
        if now:
            self._edited.add(self.project.name)
        else:
            self._edited.discard(self.project.name)
        if was != now and self.entry is not None:
            iid = str(self.entry.id)
            if self.tree.exists(iid):
                e = self.entry
                self.tree.item(iid, values=(("* " if now else "") + e.character, e.part, e.costume),
                               tags=("edited",) if now else ())

    # ------------------------------------------------------------------ selection / preview
    def _on_select(self, event=None):
        sel = self.tree.selection()
        if not sel or self._rows.get(sel[0]) is self.entry and self.project is not None:
            return
        entry = self._rows.get(sel[0])
        if entry is None:
            return
        if self._busy:
            return
        self.entry = entry
        self.var_title.set("%s - %s - %s" % (entry.character, entry.part, entry.costume))
        self.var_info.set(entry.filename)

        def work():
            proj = self.ws.open_project(self.game, entry)
            return proj, self._render(proj)
        self.run("Loading %s..." % entry.name, work, self._show_project)

    def _render(self, proj, view=None):
        model = proj.load_model()
        tex = proj.load_tex()
        cw, ch = self._canvas_size
        W, H, rgba = quickrender.render_model(model, tex, view or self.view, width=max(cw - 20, 200),
                                              max_height=max(ch - 20, 200))
        return model, rgba

    def _show_project(self, res):
        proj, (model, rgba) = res
        self.project = proj
        self._mtimes = self._project_mtimes()
        self._set_actions(True)
        slots = model.geometry_slots()
        hidden = sum(1 for s in slots if s.is_hidden)
        self.var_info.set("%s   |   texture: %s   |   %d meshes%s, %d bones   |   %s" % (
            proj.info["fed"]["filename"], proj.info["tex"]["filename"] if proj.has_tex else "none",
            len(slots), (" (%d hidden)" % hidden) if hidden else "", len(model.bones),
            "EDITED" if proj.is_modified() else "original"))
        self._set_photo(rgba)
        self._mark_edited()

    def _set_photo(self, rgba):
        png = glb.png_bytes(rgba)
        import base64
        self._photo = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
        self._redraw_photo()

    def _on_canvas_resize(self, event):
        old = self._canvas_size
        self._canvas_size = (event.width, event.height)
        self._redraw_photo()
        if self.project is not None and (abs(event.width - old[0]) > 0.15 * old[0] or
                                         abs(event.height - old[1]) > 0.15 * old[1]):
            if getattr(self, "_resize_job", None):
                self.root.after_cancel(self._resize_job)
            self._resize_job = self.root.after(500, self._rerender_after_resize)

    def _rerender_after_resize(self):
        self._resize_job = None
        if not self._busy:
            self.reload_project()

    def _redraw_photo(self):
        self.canvas.delete("all")
        if self._photo is None:
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        img = self._photo
        f = 1
        while img.height() // f > ch > 50 or img.width() // f > cw > 50:
            f += 1
        if f > 1:
            img = self._photo.subsample(f, f)
        self._shown = img
        self.canvas.create_image(cw // 2, ch // 2, image=img, anchor="center")

    def set_view(self, axis):
        self.view = axis
        self.reload_project()

    def reload_project(self):
        if self.project is None:
            return
        proj = self.project
        self.run("Rendering...", lambda: (proj, self._render(proj)), self._show_project)

    def _project_mtimes(self):
        p = self.project
        out = []
        for path in (p.fed_path, p.tex_path):
            try:
                out.append(os.path.getmtime(path) if path else 0)
            except OSError:
                out.append(0)
        return out

    def _watch_project(self):
        """Refreshes the preview when Blender saves into the project."""
        try:
            if self.project is not None and not self._busy and self._mtimes is not None:
                if self._project_mtimes() != self._mtimes:
                    self.log("Project changed on disk (saved from Blender?) - refreshing.")
                    self.reload_project()
        finally:
            self.root.after(1500, self._watch_project)

    # ------------------------------------------------------------------ actions
    def _need_project(self):
        if self.project is None:
            messagebox.showinfo("Pick a model", "Select a model in the list first.")
            return False
        return True

    def open_blender(self):
        if not self._need_project():
            return
        p = self.project
        try:
            blender_launch.launch(self.ws.settings.get("blender"), p.fed_path, p.tex_path or "",
                                  p.fed_path, p.tex_path or "")
        except Exception as e:
            messagebox.showerror("Blender", str(e))
            return
        self.var_status.set("Blender is opening... Edit, then press 'Save to project' (RecoLove panel, N key).")
        self.log("Blender started for %s" % p.name)

    def export_textures(self):
        if not self._need_project():
            return
        p = self.project
        if not p.has_tex:
            messagebox.showinfo("Textures", "This model has no texture file.")
            return

        def done(slots):
            open_folder(p.textures_dir)
            messagebox.showinfo("Textures exported",
                                "%d texture(s) saved as tex_NN.png in:\n%s\n\nEdit them, save as PNG with the "
                                "same name, then click 'Import edited textures'." %
                                (sum(1 for s in slots if s["png"]), p.textures_dir))
        self.run("Exporting textures...", p.export_textures, done)

    def import_textures(self):
        if not self._need_project():
            return
        p = self.project

        def work():
            changed = p.import_textures()
            return changed, self._render(p)

        def done(res):
            changed, rendered = res
            self._show_project((p, rendered))
            if changed:
                messagebox.showinfo("Textures", "%d texture(s) updated: %s" % (len(changed), changed))
            else:
                messagebox.showinfo("Textures", "No edited PNG found (nothing changed).")
        self.run("Importing textures...", work, done)

    def load_fed(self):
        if not self._need_project():
            return
        path = filedialog.askopenfilename(title="Pick a .fed file to use for this model",
                                          initialdir=self.project.folder, filetypes=[("RecoLove model", "*.fed")])
        if not path:
            return
        p = self.project

        def work():
            p.replace_model(path)
            return p, self._render(p)
        self.run("Loading .fed...", work, self._show_project)

    def revert(self):
        if not self._need_project():
            return
        if not messagebox.askyesno("Revert", "Discard ALL changes of this model (mesh and textures) and go back "
                                             "to the original game files?"):
            return
        p = self.project

        def work():
            p.revert()
            return p, self._render(p)
        self.run("Reverting...", work, self._show_project)

    def export_glb(self):
        if not self._need_project():
            return
        p = self.project
        out = os.path.join(p.folder, p.name + ".glb")

        def work():
            glb.export_model_glb(p.load_model(), out, p.load_tex(), p.name)
            return out

        def done(path):
            open_folder(os.path.dirname(path))
            messagebox.showinfo("glTF", "Saved:\n%s\n\nIt contains the skeleton, weights, morphs and textures. "
                                        "Open it in any glTF viewer or Blender." % path)
        self.run("Exporting .glb...", work, done)

    def build_mod(self):
        if not self.game:
            messagebox.showinfo("Game folder", "Select the game folder first.")
            return

        def done(paths):
            open_folder(self.ws.output_dir)
            messagebox.showinfo("Mod built", "Created:\n%s\n\nCopy them to your Vita:\n"
                                             "ux0:rePatch/PCSG00782/media/cpk/\n\n(see 'HOW TO INSTALL.txt')" %
                                "\n".join(os.path.basename(x) for x in paths))
        self.run("Building the mod (.cpk)...", lambda: self.ws.build_mod(self.game, log=self.log), done,
                 "Build mod")

    def install_addon(self):
        def done(res):
            ok, out = res
            self.log(out[-2000:])
            if ok:
                messagebox.showinfo("Blender add-on", "The RecoLove add-on is installed and enabled in Blender.\n"
                                                      "Find it in the 3D view sidebar (N key) > RecoLove, and in "
                                                      "File > Import/Export.")
            else:
                messagebox.showwarning("Blender add-on", "Could not confirm the installation - see the log.\n"
                                                         "You can install it by hand: Blender > Edit > Preferences > "
                                                         "Get Extensions > Install from Disk > io_recolove.zip "
                                                         "(use Settings > Save add-on .zip).")
        self.run("Installing the Blender add-on...",
                 lambda: blender_launch.install_addon(self.ws.settings.get("blender")), done, "Blender add-on")

    # ------------------------------------------------------------------ dialogs
    def show_help(self, tab=None):
        HelpWindow(self.root, tab)

    def settings_dialog(self):
        SettingsDialog(self)

    def swap_dialog(self):
        if not self._need_project():
            return
        if self.entry.part != "Body":
            messagebox.showinfo("DOA5 body swap", "Pick a BODY model (Part = Body) for the body swap.")
            return
        SwapDialog(self)

    def material_dialog(self):
        if not self._need_project():
            return
        MaterialDialog(self)


# ---------------------------------------------------------------------- dialogs

def thumbnail(model, tex, slots, size=220):
    W, H, rgba = quickrender.render_model(model, tex, "xy", width=size, max_height=size + 60, only_slots=slots)
    import base64
    return tk.PhotoImage(data=base64.b64encode(glb.png_bytes(rgba)).decode("ascii"))


class HelpWindow(object):
    def __init__(self, root, tab=None):
        self.win = w = tk.Toplevel(root)
        w.title("Help - %s" % helptext.APP_NAME)
        w.geometry("760x620")
        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        select = None
        for title, text in helptext.TABS:
            f = ttk.Frame(nb)
            t = tk.Text(f, wrap="word", font=("Segoe UI", 10), padx=12, pady=10, relief="flat")
            sb = ttk.Scrollbar(f, command=t.yview)
            t.configure(yscrollcommand=sb.set)
            t.insert("1.0", text)
            t.configure(state="disabled")
            sb.pack(side="right", fill="y")
            t.pack(fill="both", expand=True)
            nb.add(f, text=title)
            if title == tab:
                select = f
        if select is not None:
            nb.select(select)
        ttk.Button(w, text="Close", command=w.destroy).pack(pady=(0, 8))


class SettingsDialog(object):
    def __init__(self, app):
        self.app = app
        self.win = w = tk.Toplevel(app.root)
        w.title("Settings")
        w.geometry("640x260")
        w.transient(app.root)
        pad = {"padx": 8, "pady": 6}
        frm = ttk.Frame(w, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="Blender (blender.exe):").grid(row=0, column=0, sticky="w", **pad)
        self.var_blender = tk.StringVar(value=app.ws.settings.get("blender") or "")
        ttk.Entry(frm, textvariable=self.var_blender).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse...", command=self.browse).grid(row=0, column=2, **pad)
        found = blender_launch.find_blender(self.var_blender.get())
        ttk.Label(frm, text="Detected: %s" % (found or "not found - install Blender 4.2+"),
                  style="Sub.TLabel").grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(frm, text="Workspace (your projects):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Label(frm, text=app.ws.root).grid(row=2, column=1, sticky="w", **pad)
        ttk.Button(frm, text="Open", command=lambda: open_folder(app.ws.root)).grid(row=2, column=2, **pad)
        ttk.Label(frm, text="Blender add-on file:").grid(row=3, column=0, sticky="w", **pad)
        ttk.Button(frm, text="Save add-on .zip...", command=self.save_zip).grid(row=3, column=1, sticky="w", **pad)
        b = ttk.Frame(frm)
        b.grid(row=4, column=0, columnspan=3, sticky="e", pady=12)
        ttk.Button(b, text="Save", command=self.save).pack(side="right", padx=4)
        ttk.Button(b, text="Cancel", command=w.destroy).pack(side="right", padx=4)

    def browse(self):
        p = filedialog.askopenfilename(title="blender.exe", filetypes=[("blender.exe", "blender.exe"), ("Program", "*.exe")])
        if p:
            self.var_blender.set(p)

    def save_zip(self):
        import shutil
        p = filedialog.asksaveasfilename(title="Save the Blender add-on", initialfile="io_recolove.zip",
                                         defaultextension=".zip", filetypes=[("zip", "*.zip")])
        if p:
            shutil.copyfile(blender_launch.addon_zip_path(), p)
            messagebox.showinfo("Add-on", "Saved. In Blender: Edit > Preferences > Get Extensions > "
                                          "(v menu) > Install from Disk...", parent=self.win)

    def save(self):
        self.app.ws.settings["blender"] = self.var_blender.get().strip()
        self.app.ws.save_settings()
        self.win.destroy()


class SwapDialog(object):
    def __init__(self, app):
        self.app = app
        self.proj = app.project
        self.model = self.proj.load_model()
        self.tex = self.proj.load_tex()
        try:
            self.sug = doa_swap.suggest_params(self.model, self.tex, doa_hands=True)
            self.hand_slots = doa_swap.hand_slots(self.model)
        except doa_swap.SwapError as e:
            messagebox.showerror("DOA5 body swap", str(e))
            return
        skin, mat, hide, _ref = self.sug
        self.win = w = tk.Toplevel(app.root)
        w.title("Replace body with a DOA5 model")
        w.geometry("900x690")
        w.transient(app.root)
        main = ttk.Frame(w, padding=10)
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(main, width=260)
        right.pack(side="right", fill="y", padx=(10, 0))

        ttk.Label(left, text="1. DOA5 model file (.TMC - keep the .TMCL next to it)").pack(anchor="w")
        row = ttk.Frame(left)
        row.pack(fill="x", pady=2)
        self.var_tmc = tk.StringVar(value=app.ws.settings.get("last_tmc", ""))
        ttk.Entry(row, textvariable=self.var_tmc).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self.browse).pack(side="left", padx=4)
        row = ttk.Frame(left)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Object:").pack(side="left")
        self.var_obj = tk.StringVar()
        self.cb_obj = ttk.Combobox(row, textvariable=self.var_obj, state="readonly", width=40)
        self.cb_obj.pack(side="left", padx=4)

        ttk.Label(left, text="2. Meshes of this model (click a row to see it)").pack(anchor="w", pady=(10, 0))
        cols = ("slot", "use", "info")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=12)
        for c, wdt, t in zip(cols, (50, 110, 330), ("Slot", "Action", "Mesh")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=wdt, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_slot)
        self.tree.bind("<Double-1>", self.cycle_action)
        self.actions = {}
        for s in self.model.geometry_slots():
            if s.is_hidden:
                continue
            act = "NEW BODY" if s.index == skin else ("hide" if s.index in hide else "keep")
            self.actions[s.index] = act
            nv = len(s.uniq_pos)
            kind = "RecoLove hand" if s.index in self.hand_slots else (
                "skinned" if s.is_skinned else "rigid (accessory)")
            info = "%s, %d vertices, material %s" % (kind, nv, ",".join(map(str, s.material_ids)))
            self.tree.insert("", "end", iid=str(s.index), values=(s.index, act, info))
        ttk.Label(left, text="Double-click a row to change its action: NEW BODY (receives the DOA body, only one), "
                             "hide or keep.", style="Sub.TLabel", wraplength=560, justify="left").pack(anchor="w")

        opt = ttk.Frame(left)
        opt.pack(fill="x", pady=8)
        ttk.Label(opt, text="3. Texture for the new body:").grid(row=0, column=0, sticky="w")
        mats = ["Automatic (material %s)" % mat if mat is not None else "Automatic (adds a new material)"]
        for m in self.model.materials:
            if m.textured:
                mats.append("Material %d (texture slot %s)" % (m.index, m.texture_slot(self.tex)))
        mats.append("Add a new material (experimental)")
        self.var_mat = tk.StringVar(value=mats[0])
        self.cb_mat = ttk.Combobox(opt, textvariable=self.var_mat, values=mats, state="readonly", width=38)
        self.cb_mat.grid(row=0, column=1, padx=6)
        ttk.Label(opt, text="Texture size:").grid(row=1, column=0, sticky="w", pady=4)
        self.var_size = tk.StringVar(value="1024")
        ttk.Combobox(opt, textvariable=self.var_size, values=["512", "1024", "2048"], state="readonly",
                     width=8).grid(row=1, column=1, sticky="w", padx=6)
        self.var_tone = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Match the skin tone of the RecoLove head/hands", variable=self.var_tone).grid(
            row=2, column=0, columnspan=2, sticky="w")
        self.var_hands = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Use the DOA hands (seamless wrists; fingers don't animate) - untick to keep "
                                  "the RecoLove hands",
                        variable=self.var_hands,
                        command=self.toggle_hands).grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Label(right, text="Preview of the selected mesh").pack(anchor="w")
        self.thumb = tk.Label(right, bg="#2b2b2b", width=240, height=300)
        self.thumb.pack(fill="x", pady=4)
        ttk.Label(right, text="Hands: with 'Use the DOA hands' the DOA hands stay attached to the body and the "
                              "RecoLove hands are hidden (no finger poses). Without it the DOA hands are cut off "
                              "and the RecoLove hands (with finger poses) are kept. Changing a material of a "
                              "hidden mesh affects nothing visible.",
                  style="Sub.TLabel", wraplength=250, justify="left").pack(anchor="w", pady=6)
        ttk.Button(right, text="Replace body", style="Big.TButton", command=self.apply).pack(fill="x", side="bottom")
        ttk.Button(right, text="Cancel", command=w.destroy).pack(fill="x", side="bottom", pady=4)
        if self.var_tmc.get():
            self.load_objects()
        self.tree.selection_set(str(skin))

    def toggle_hands(self):
        for i in self.hand_slots:
            if i in self.actions and self.actions[i] != "NEW BODY":
                self.actions[i] = "hide" if self.var_hands.get() else "keep"
                self.tree.set(str(i), "use", self.actions[i])
        mat = doa_swap.suggest_params(self.model, self.tex, doa_hands=self.var_hands.get())[1]
        auto = "Automatic (material %s)" % mat if mat is not None else "Automatic (adds a new material)"
        vals = [auto] + list(self.cb_mat.cget("values"))[1:]
        was_auto = self.var_mat.get().startswith("Automatic")
        self.cb_mat.configure(values=vals)
        if was_auto:
            self.var_mat.set(auto)

    def browse(self):
        p = filedialog.askopenfilename(parent=self.win, title="Pick the DOA5 .TMC file",
                                       filetypes=[("DOA5 model", "*.tmc *.TMC")])
        if p:
            self.var_tmc.set(p)
            self.load_objects()

    def load_objects(self):
        try:
            objs = doa_swap.list_objects(self.var_tmc.get())
        except Exception as e:
            messagebox.showerror("TMC", "Could not read this file:\n%s" % e, parent=self.win)
            return
        vals = ["%s  (%d vertices)" % (n, v) for n, v in objs]
        self.cb_obj.configure(values=vals)
        if vals:
            self.var_obj.set(vals[0])

    def show_slot(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            self._photo = thumbnail(self.model, self.tex, [int(sel[0])], 240)
            self.thumb.configure(image=self._photo, width=240, height=300)
        except Exception:
            pass

    def cycle_action(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        i = int(sel[0])
        order = ["keep", "hide", "NEW BODY"]
        new = order[(order.index(self.actions[i]) + 1) % 3]
        if new == "NEW BODY":
            for k, v in self.actions.items():
                if v == "NEW BODY":
                    self.actions[k] = "hide"
                    self.tree.set(str(k), "use", "hide")
        self.actions[i] = new
        self.tree.set(sel[0], "use", new)

    def apply(self):
        tmc = self.var_tmc.get()
        if not tmc or not os.path.isfile(tmc):
            messagebox.showinfo("DOA5 body swap", "Pick the .TMC file first.", parent=self.win)
            return
        skin = [k for k, v in self.actions.items() if v == "NEW BODY"]
        if not skin:
            messagebox.showinfo("DOA5 body swap", "Mark one mesh as NEW BODY (double-click a row).", parent=self.win)
            return
        hide = [k for k, v in self.actions.items() if v == "hide"]
        mv = self.var_mat.get()
        if mv.startswith("Automatic"):
            material = None
        elif mv.startswith("Add a new"):
            material = "new"
        else:
            material = int(mv.split()[1])
        obj = self.var_obj.get().split("  (")[0] or None
        size = int(self.var_size.get())
        tone = self.var_tone.get()
        doa_hands = self.var_hands.get()
        app, p = self.app, self.proj
        app.ws.settings["last_tmc"] = tmc
        app.ws.save_settings()
        self.win.destroy()

        def work():
            info = doa_swap.swap_body(p.fed_path, p.tex_path, tmc, p.fed_path, p.tex_path, slot=skin[0],
                                      material=material, hide=hide, tex_size=size, object_name=obj,
                                      match_skin=tone, doa_hands=doa_hands, log=app.log)
            return info, (p, app._render(p))

        def done(res):
            info, shown = res
            app._show_project(shown)
            messagebox.showinfo("DOA5 body swap", "Done! The body of slot %d was replaced (%d vertices) and %d "
                                                  "mesh(es) were hidden.\n\nCheck it (Open in Blender lets you "
                                                  "pose it), then Build mod." %
                                (info["slot"], info["vertices"], len(info["hidden"])))
        app.run("Replacing the body...", work, done, "DOA5 body swap")


class MaterialDialog(object):
    def __init__(self, app):
        self.app = app
        self.proj = app.project
        self.model = self.proj.load_model()
        self.tex = self.proj.load_tex()
        self.win = w = tk.Toplevel(app.root)
        w.title("Change material")
        w.geometry("820x480")
        w.transient(app.root)
        main = ttk.Frame(w, padding=10)
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="The material decides the texture (and shader) of a mesh part. A famous use: make "
                             "a swimsuit use the skin material so it shades like skin.", wraplength=520,
                  justify="left").pack(anchor="w")
        cols = ("slot", "sub", "mat", "info")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
        for c, wdt, t in zip(cols, (50, 70, 70, 300), ("Slot", "Part", "Material", "Mesh")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=wdt, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=6)
        self.tree.bind("<<TreeviewSelect>>", self.show)
        self.fill()
        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Label(row, text="New material:").pack(side="left")
        vals = ["%d (texture slot %s)" % (m.index, m.texture_slot(self.tex)) for m in self.model.materials]
        self.var_new = tk.StringVar()
        ttk.Combobox(row, textvariable=self.var_new, values=vals, state="readonly", width=24).pack(side="left", padx=6)
        ttk.Button(row, text="Apply", command=self.apply).pack(side="left")
        ttk.Button(row, text="Close", command=w.destroy).pack(side="right")
        self.thumb = tk.Label(main, bg="#2b2b2b", width=240, height=300)
        self.thumb.pack(side="right", fill="y", padx=(10, 0))

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for s in self.model.geometry_slots():
            if s.is_hidden:
                continue
            for k, sm in enumerate(s.submeshes):
                self.tree.insert("", "end", iid="%d:%d" % (s.index, k),
                                 values=(s.index, k, sm.material_id, "%d vertices, %s" % (
                                     len(sm.positions), "skinned" if s.is_skinned else "rigid")))

    def show(self, event=None):
        sel = self.tree.selection()
        if sel:
            try:
                self._photo = thumbnail(self.model, self.tex, [int(sel[0].split(":")[0])], 240)
                self.thumb.configure(image=self._photo)
            except Exception:
                pass

    def apply(self):
        sel = self.tree.selection()
        if not sel or not self.var_new.get():
            messagebox.showinfo("Material", "Select a row and a new material.", parent=self.win)
            return
        slot, sub = (int(x) for x in sel[0].split(":"))
        new = int(self.var_new.get().split()[0])
        model = fedmodel.Model.load(self.proj.fed_path)
        old = model.set_submesh_material(slot, sub, new)
        model.save(self.proj.fed_path)
        self.model = model
        self.fill()
        self.app.log("slot %d part %d: material %d -> %d" % (slot, sub, old, new))
        self.app.reload_project()


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
