"""Blender RenderEngine integration: exports to RDLA and runs moonray.

NOTE: Blender 5.2 alpha's RenderEngine Python proxy no longer supports
storing attributes on engine instances (any instance-dict access raises
"ReferenceError: StructRNA ... has been removed"), while the render()
methods (report/update_stats/test_break/begin_result/...) all work.
This engine therefore keeps ALL state in local variables.
"""

import os
import shutil
import tempfile
import time

import bpy

from . import exporter
from .renderer import MoonRayProcess, resolve_moonray_root

ADDON_ID = __package__.split(".")[0]


class MoonRayRenderEngine(bpy.types.RenderEngine):
    bl_idname = "MOONRAY_RENDER"
    bl_label = "MoonRay"
    bl_use_preview = False
    bl_use_shading_nodes = True
    bl_use_shading_nodes_custom = False

    def __init__(self, *args):
        # Blender 5.x passes engine-creation arguments; no instance
        # attributes may be stored (see module docstring).
        pass

    # -- helpers -----------------------------------------------------------
    def _prefs(self):
        addon = bpy.context.preferences.addons.get(ADDON_ID)
        return addon.preferences if addon is not None else None

    def _report_error(self, msg):
        self.report({"ERROR"}, msg)

    def _keep_rdla(self, rdla_path, scene, settings):
        """Copy the temporary RDLA scene to the user-chosen location."""
        if not settings.keep_rdla:
            return
        target = settings.rdla_path
        if not target:
            out = scene.render.filepath
            if not out:
                return
            target = os.path.splitext(out)[0] + ".rdla"
        try:
            target_dir = os.path.dirname(os.path.abspath(target))
            if target_dir and not os.path.isdir(target_dir):
                os.makedirs(target_dir, exist_ok=True)
            shutil.copyfile(rdla_path, target)
            self.report({"INFO"}, "Saved RDLA scene to %s" % target)
        except Exception as e:
            self.report({"WARNING"}, "Could not save RDLA scene: %s" % e)

    # -- RenderEngine API --------------------------------------------------
    def render(self, depsgraph):
        try:
            self._render_impl(depsgraph)
        except ReferenceError:
            # Blender 5.2 alpha invokes render() a second time after the
            # engine struct has been released; nothing can be done then.
            pass

    def _render_impl(self, depsgraph):
        scene = depsgraph.scene_eval
        settings = scene.moonray
        prefs = self._prefs()

        if prefs is None:
            self._report_error("MoonRay add-on preferences not found")
            return

        root, err = resolve_moonray_root(prefs.moonray_root)
        if err:
            self._report_error("MoonRay not found (%s). Set the correct "
                               "installation path in the add-on preferences."
                               % err)
            return

        w = max(1, int(scene.render.resolution_x
                       * scene.render.resolution_percentage / 100.0))
        h = max(1, int(scene.render.resolution_y
                       * scene.render.resolution_percentage / 100.0))

        tmpdir = tempfile.mkdtemp(prefix="moonray_")
        out_exr = os.path.join(tmpdir,
                               "frame_%04d.exr" % scene.frame_current)

        def cleanup():
            if tmpdir and not (prefs.debug_keep_files):
                shutil.rmtree(tmpdir, ignore_errors=True)

        # 1. export the scene to RDLA
        self.update_stats("Exporting", "MoonRay: writing scene")
        try:
            rdla_path = exporter.export_scene(
                scene, depsgraph, settings, prefs, out_exr,
                report=lambda msg: self.report({"WARNING"}, msg))
        except Exception as e:
            self._report_error("Export failed: %s" % e)
            cleanup()
            return

        if settings.export_only:
            self.report({"INFO"}, "Exported scene to %s" % rdla_path)
            self._keep_rdla(rdla_path, scene, settings)
            cleanup()
            return

        # optionally persist the intermediate RDLA scene
        self._keep_rdla(rdla_path, scene, settings)

        # 2. render with the moonray CLI
        proc = MoonRayProcess(root, prefs.installs_root)
        args = ["-in", rdla_path]
        if settings.threads > 0:
            args += ["-threads", str(settings.threads)]

        def on_progress(pct):
            self.update_progress(pct / 100.0)
            self.update_stats("Rendering", "MoonRay: %d%%" % pct)

        try:
            proc.launch(args, progress_cb=on_progress)
        except OSError as e:
            self._report_error("Could not launch moonray: %s" % e)
            cleanup()
            return

        try:
            while proc.proc.poll() is None:
                if self.test_break():
                    proc.kill()
                    cleanup()
                    return
                time.sleep(0.1)
            rc = proc.proc.returncode
        finally:
            pass

        if rc != 0:
            tail = "\n".join(proc.error_lines[-10:])
            self._report_error("moonray failed (exit code %d).\n%s"
                               % (rc, tail))
            cleanup()
            return
        self.update_progress(1.0)

        final = out_exr
        if settings.use_denoise and os.path.isfile(proc.denoise_bin):
            self.update_stats("Denoising", "MoonRay: OIDN denoise")
            denoised = os.path.join(tmpdir, "denoised.exr")
            try:
                proc.run_denoise(out_exr, denoised)
                final = denoised
            except Exception as e:
                self.report({"WARNING"}, "Denoise failed (%s); "
                                         "using raw render" % e)

        # 3. load the result into the Render Result
        result = self.begin_result(0, 0, w, h)
        if not result.layers:
            self._report_error("No render layers available for the result")
            self.end_result(result)
            cleanup()
            return
        layer = result.layers[0]
        try:
            layer.load_from_file(final)
        except Exception as e:
            self._report_error("Could not read render output: %s" % e)
        self.end_result(result)
        cleanup()
