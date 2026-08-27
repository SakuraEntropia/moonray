"""End-to-end test: enable add-on, render a Blender scene with MoonRay.

Requires a working MoonRay installation (set MOONRAY_ROOT env or edit below).

Run:
  /Applications/Blender.app/Contents/MacOS/Blender --background \
      --factory-startup --python blender_addon/tests/test_render.py -- <out.png>
"""

import os
import shutil
import sys
import tempfile

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.dirname(HERE)

INSTALLS_ROOT = os.environ.get(
    "MOONRAY_INSTALLS",
    "/Users/faputa/Documents/wave-tracer/installs")
MOONRAY_ROOT = os.environ.get(
    "MOONRAY_ROOT",
    os.path.join(INSTALLS_ROOT, "openmoonray"))


def main(out_path):
    # install under canonical module name and enable through the add-on flow
    tmp = tempfile.mkdtemp(prefix="moonray_render_test_")
    pkg_dir = os.path.join(tmp, "moonray_blender")
    shutil.copytree(ADDON_DIR, pkg_dir,
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    sys.path.insert(0, tmp)
    bpy.ops.preferences.addon_enable(module="moonray_blender")

    prefs = bpy.context.preferences.addons["moonray_blender"].preferences
    prefs.moonray_root = MOONRAY_ROOT
    prefs.installs_root = INSTALLS_ROOT
    prefs.debug_keep_files = False
    print("MOONRAY_ROOT:", MOONRAY_ROOT)
    print("BIN EXISTS:", os.path.isfile(os.path.join(MOONRAY_ROOT, "bin", "moonray")))

    # build a small scene
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "MOONRAY_RENDER"
    scene.render.resolution_x = 480
    scene.render.resolution_y = 270
    scene.render.resolution_percentage = 100
    scene.render.filepath = out_path
    scene.render.image_settings.file_format = "PNG"

    bpy.ops.mesh.primitive_uv_sphere_add(location=(0, 0, 1))
    bpy.ops.object.light_add(type="SUN", location=(5, 5, 8))
    bpy.context.object.data.energy = 3.0
    bpy.ops.object.camera_add(location=(5, -5, 3))
    cam = bpy.context.object
    cam.rotation_euler = (1.2, 0, 0.8)
    scene.camera = cam

    settings = scene.moonray
    settings.pixel_samples = 6
    settings.max_adaptive_samples = 64
    settings.threads = 8

    # render
    bpy.ops.render.render(write_still=True)

    ok = os.path.isfile(out_path) and os.path.getsize(out_path) > 1000
    print("RENDER RESULT:", "OK" if ok else "MISSING",
          out_path, os.path.getsize(out_path) if os.path.exists(out_path) else 0)

    bpy.ops.preferences.addon_disable(module="moonray_blender")
    return 0 if ok else 1


if __name__ == "__main__":
    argv = sys.argv
    out = None
    if "--" in argv:
        out = argv[argv.index("--") + 1]
    sys.exit(main(out or "/tmp/moonray_blender_render.png"))
