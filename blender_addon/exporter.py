"""Export a Blender scene to the MoonRay RDLA scene description format.

Coordinate conventions
----------------------
Blender      : Z-up, right-handed; cameras look along local -Z; lights emit
               along local -Z.
MoonRay      : Y-up, right-handed; cameras look along local -Z; lights emit
               along local +Z.

The axis swap A maps Blender coordinates to MoonRay coordinates:
    m = A @ b      A = [[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]

Transforms exported:
    camera   : node_xform = A @ M_world          (local conventions match)
    geometry : node_xform = A @ M_world @ A^-1   (mesh data left untouched)
    light    : node_xform = A @ M_world @ F      (F flips Z: +Z emits)
"""

import math
import os

import bpy
from mathutils import Matrix

# ---------------------------------------------------------------------------
# Matrices

_A = Matrix(((1, 0, 0, 0),
             (0, 0, 1, 0),
             (0, -1, 0, 0),
             (0, 0, 0, 1)))

_A_INV = Matrix(((1, 0, 0, 0),
                 (0, 0, -1, 0),
                 (0, 1, 0, 0),
                 (0, 0, 0, 1)))

_F = Matrix(((1, 0, 0, 0),
             (0, 1, 0, 0),
             (0, 0, -1, 0),
             (0, 0, 0, 1)))


def camera_xform(m):
    return _A @ m


def geometry_xform(m):
    return _A @ m @ _A_INV


def light_xform(m):
    return _A @ m @ _F


def _env_rotation_matrix(theta):
    """Rotation about the up (Y) axis for the EnvLight node_xform."""
    import math as _math
    c, s = _math.cos(theta), _math.sin(theta)
    return Matrix(((c, 0.0, s, 0.0),
                   (0.0, 1.0, 0.0, 0.0),
                   (-s, 0.0, c, 0.0),
                   (0.0, 0.0, 0.0, 1.0)))


_LIGHT_CLASS = {
    "POINT": "SphereLight",
    "SUN": "DistantLight",
    "SPOT": "SpotLight",
    "AREA": "RectLight",
}


# ---------------------------------------------------------------------------
# Formatting helpers

def _f(v):
    """Format a float for RDLA (plain decimal, no trailing 'f')."""
    if abs(v) < 1e-30:
        return "0"
    return "%.9g" % v


def fmt_vec2(v):
    return "Vec2(%s, %s)" % (_f(v[0]), _f(v[1]))


def fmt_vec3(v):
    return "Vec3(%s, %s, %s)" % (_f(v[0]), _f(v[1]), _f(v[2]))


def fmt_rgb(c):
    return "Rgb(%s, %s, %s)" % (_f(c[0]), _f(c[1]), _f(c[2]))


def fmt_mat4(m):
    vals = ", ".join(_f(m[i][j]) for i in range(4) for j in range(4))
    return "Mat4(%s)" % vals


def fmt_string(s):
    return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"')


def sanitize_name(name, fallback="unnamed"):
    """Make a Blender name safe to embed in RDLA code.

    The sanitized name is used both inside RDLA string literals and as a
    bare Lua variable name (``mesh_X = RdlMeshGeometry("mesh_X") { ... }``),
    so it must be a valid Lua identifier: alphanumerics and underscore only.
    Blender auto-names duplicates like "Sphere.001"; the '.' (and '-', '/')
    would otherwise be parsed by Lua as operators and abort with
    "malformed number near '.001_'".
    """
    name = str(name).strip() or fallback
    out = []
    for ch in name:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)



class MoonRayExporter:
    def __init__(self, scene, depsgraph, settings, prefs, out_path, report=print):
        self.scene = scene
        self.depsgraph = depsgraph
        self.settings = settings
        self.prefs = prefs
        self.out_path = out_path
        self.report = report
        self.lines = []
        self.indent = 0
        self.geo_count = 0
        self.mat_count = 0
        self._last_geo_name = None
        self._last_mat_name = None
        self.light_refs = []  # RDLA references for the LightSet block

    # -- low-level writers ------------------------------------------------
    def out(self, line=""):
        if line:
            self.lines.append("    " * self.indent + line)
        else:
            self.lines.append("")

    def block(self, header):
        self.out(header + " {")
        self.indent += 1

    def block_assigned(self, varname, header):
        """Object definition with a Lua variable assignment.

        The official test scenes reference layer objects through Lua
        variables (varname = ClassName("name") { ... }); referencing
        standalone blocks through constructor calls does not resolve
        reliably in rdl2, which silently renders nothing.
        """
        self.out(varname + " = " + header + " {")
        self.indent += 1

    def end_block(self):
        self.indent -= 1
        self.out("}")

    def unique(self, base):
        self.geo_count += 1
        return "%s_%d" % (base, self.geo_count)

    # -- scene components --------------------------------------------------
    def write_scene_variables(self):
        scene = self.scene
        render = scene.render
        w = max(1, int(render.resolution_x * render.resolution_percentage / 100.0))
        h = max(1, int(render.resolution_y * render.resolution_percentage / 100.0))

        s = self.settings
        self.block("SceneVariables")
        self.out('["camera"] = PerspectiveCamera("camera"),')
        self.out('["image_width"] = %d,' % w)
        self.out('["image_height"] = %d,' % h)
        self.out('["output_file"] = %s,' % fmt_string(self.out_path))
        self.out('["res"] = 1,')
        self.out('["frame"] = %s,' % _f(scene.frame_current))
        self.out('["pixel_samples"] = %d,' % int(s.pixel_samples))
        self.out('["min_adaptive_samples"] = %d,' % int(s.min_adaptive_samples))
        self.out('["max_adaptive_samples"] = %d,' % int(s.max_adaptive_samples))
        if s.pixel_filter != "DEFAULT":
            self.out('["pixel_filter"] = %d,'
                     % {"BOX": 0, "CUBIC": 1, "QUADRATIC": 2}[s.pixel_filter])
        if abs(s.pixel_filter_width - 3.0) > 1e-6:
            self.out('["pixel_filter_width"] = %s,' % _f(s.pixel_filter_width))
        if s.use_progressive_tiles:
            self.out('["progressive_tile_order"] = 4,')
        self.end_block()

    def write_camera(self):
        cam_obj = self.scene.camera
        if cam_obj is None:
            self.report("WARNING: no active camera in scene")
            return
        cam = cam_obj.data
        evaluated = cam_obj.evaluated_get(self.depsgraph)
        m = evaluated.matrix_world

        self.block('PerspectiveCamera("camera")')
        self.out('["node_xform"] = %s,' % fmt_mat4(camera_xform(m)))
        self.out('["focal"] = %s,' % _f(cam.lens))
        self.out('["film_width_aperture"] = %s,' % _f(cam.sensor_width))
        self.out('["near"] = %s,' % _f(max(1e-4, cam.clip_start)))
        self.out('["far"] = %s,' % _f(cam.clip_end))
        if abs(cam.shift_x) > 1e-6 or abs(cam.shift_y) > 1e-6:
            self.out('["horizontal_film_offset"] = %s,'
                     % _f(cam.shift_x * cam.sensor_width))
            self.out('["vertical_film_offset"] = %s,'
                     % _f(cam.shift_y * cam.sensor_width))
        if self.settings.use_motion_blur:
            self.out('["mb_shutter_open"] = -0.5,')
            self.out('["mb_shutter_close"] = 0.5,')
        if cam.dof.use_dof:
            self.out('["dof"] = true,')
            fstop = max(0.05, cam.dof.aperture_fstop)
            self.out('["dof_aperture"] = %s,' % _f(cam.lens / fstop))
            self.out('["dof_focus_distance"] = %s,'
                     % _f(cam.dof.focus_distance))
        self.end_block()

    def write_world(self):
        world = self.scene.world
        color = (0.05, 0.05, 0.05)
        strength = 1.0
        env_texture = None
        env_rotation = 0.0
        if world is not None and world.use_nodes:
            bg = next((n for n in world.node_tree.nodes
                       if n.type == "BACKGROUND"), None)
            if bg is not None:
                color_in = bg.inputs["Color"]
                if color_in.is_linked:
                    src = color_in.links[0].from_node
                    if (src.type == "TEX_ENVIRONMENT"
                            and src.image is not None
                            and src.image.filepath):
                        env_texture = src.image
                        env_rotation = self._env_mapping_rotation(src)
                try:
                    color = tuple(color_in.default_value)[:3]
                    strength = float(bg.inputs["Strength"].default_value)
                except Exception:
                    pass
        self.block_assigned('envlight', 'EnvLight("envlight")')
        if env_texture is not None:
            self.out('["texture"] = %s,' % fmt_string(
                bpy.path.abspath(env_texture.filepath)))
        if abs(env_rotation) > 1e-6:
            self.out('["node_xform"] = %s,'
                     % fmt_mat4(_env_rotation_matrix(env_rotation)))
        self.out('["color"] = %s,' % fmt_rgb(
            tuple(c * strength for c in color)))
        self.out('["intensity"] = 1,')
        self.end_block()
        self.light_refs.append('envlight')

    def _env_mapping_rotation(self, env_tex_node):
        """Rotation (radians) of a Mapping node driving the environment
        texture; 0.0 when absent."""
        try:
            vec_in = env_tex_node.inputs["Vector"]
            if vec_in.is_linked:
                src = vec_in.links[0].from_node
                if src.type == "MAPPING":
                    return float(src.inputs["Rotation"].default_value[2])
        except Exception:
            pass
        return 0.0

    # -- lights ------------------------------------------------------------
    def write_lights(self):
        for obj in self.scene.objects:
            if obj.type != "LIGHT" or not obj.visible_get():
                continue
            light = obj.data
            if light.type not in _LIGHT_CLASS or light.energy <= 0.0:
                continue
            self.geo_count += 1
            name = "light_%s_%d" % (sanitize_name(obj.name), self.geo_count)
            self._write_one_light(obj, light, name)
            self.light_refs.append(name)

        self.block_assigned('lightset', 'LightSet("lightset")')
        for ref in self.light_refs:
            self.out(ref + ",")
        self.end_block()

    def _write_one_light(self, obj, light, name):
        evaluated = obj.evaluated_get(self.depsgraph)
        m = evaluated.matrix_world
        scale = self.prefs.light_scale
        color = tuple(light.color)

        cls = _LIGHT_CLASS[light.type]
        self.block_assigned(name, '%s("%s")' % (cls, name))
        self.out('["node_xform"] = %s,' % fmt_mat4(light_xform(m)))

        if light.type == "AREA":
            sx = max(1e-6, light.size)
            sy = max(1e-6, light.size_y)
            # Blender area energy is in W; MoonRay normalized RectLight
            # intensity is radiance-like, so divide by area.
            intensity = (light.energy * scale) / (sx * sy)
            self.out('["width"] = %s,' % _f(sx))
            self.out('["height"] = %s,' % _f(sy))
        elif light.type == "POINT":
            # Blender point energy is in W; a normalized SphereLight
            # intensity of energy/(4*pi) approximates the same emission.
            intensity = (light.energy * scale) / (4.0 * math.pi)
            self.out('["radius"] = %s,' % _f(max(1e-6, light.shadow_soft_size)))
        elif light.type == "SPOT":
            outer = light.spot_size * 0.5  # half angle in radians
            inner = outer * (1.0 - max(0.0, min(1.0, light.spot_blend)))
            intensity = light.energy * scale
            self.out('["inner_cone_angle"] = %s,' % _f(math.degrees(inner)))
            self.out('["outer_cone_angle"] = %s,' % _f(math.degrees(outer)))
            if light.shadow_soft_size > 0.0:
                self.out('["lens_radius"] = %s,' % _f(light.shadow_soft_size))
        elif light.type == "SUN":
            intensity = light.energy * scale
            self.out('["angular_extent"] = %s,' % _f(math.degrees(light.angle)))

        self.out('["color"] = %s,' % fmt_rgb(color))
        self.out('["intensity"] = %s,' % _f(intensity))
        self.out('["exposure"] = 0,')
        self.out('["normalized"] = true,')
        self.out('["visible_in_camera"] = "force off",')
        self.end_block()

    # -- geometry ----------------------------------------------------------
    def write_meshes(self):
        depsgraph = self.depsgraph
        entries = []

        # group objects: shared data blocks (linked duplicates) without
        # modifiers can be instanced with a single RdlInstancerGeometry
        grouped = {}  # key -> list of (obj, evaluated, mesh)
        for obj in self.scene.objects:
            if obj.type not in ("MESH", "CURVE", "SURFACE", "FONT", "META",
                                "CURVES", "POINTCLOUD"):
                continue
            if not obj.visible_get():
                continue
            evaluated = obj.evaluated_get(depsgraph)
            try:
                mesh = evaluated.to_mesh()
            except RuntimeError:
                continue
            if mesh is None or len(mesh.polygons) == 0:
                if mesh is not None:
                    evaluated.to_mesh_clear()
                continue
            if not obj.modifiers:
                mat = obj.active_material
                key = ("data", id(obj.data), mat.name_full if mat else "")
            else:
                key = ("obj", id(obj))
            grouped.setdefault(key, []).append((obj, evaluated, mesh))

        for key, items in grouped.items():
            try:
                if len(items) == 1:
                    obj, evaluated, mesh = items[0]
                    geo_name, mat_name = self._write_one_mesh(
                        obj, evaluated, mesh)
                else:
                    geo_name, mat_name = self._write_instancer(items)
                entries.append((geo_name, mat_name))
            finally:
                for _obj, _evaluated, _mesh in items:
                    _evaluated.to_mesh_clear()

        if entries:
            self.block('Layer("defaultLayer")')
            for geo_ref, mat_name in entries:
                self.out('{%s, "", %s, lightset, undef(), undef(), undef(), undef()},'
                         % (geo_ref, mat_name))
            self.end_block()

    def _write_instancer(self, items):
        """Export one RdlMeshGeometry + one RdlInstancerGeometry for a group
        of objects sharing the same mesh data and material."""
        obj0, evaluated0, mesh0 = items[0]
        name_base = sanitize_name(obj0.data.name or obj0.name, "mesh")
        base_name = self.unique("instbase_" + name_base)
        inst_name = self.unique("inst_" + name_base)
        geo_name = self.unique("geo_" + name_base)
        mat_name = self.unique("mat_" + name_base)
        self._last_geo_name = geo_name
        self._last_mat_name = mat_name

        # base geometry: identity transform, instancer places the instances
        mesh = mesh0
        mesh.calc_loop_triangles()
        tris = mesh.loop_triangles
        corners = mesh.corners if hasattr(mesh, "corners") else mesh.loops
        if hasattr(mesh, "calc_normals_split"):
            mesh.calc_normals_split()
        uv_layer = mesh.uv_layers.active
        has_uvs = uv_layer is not None

        positions = []
        uvs = []
        normals = []
        indices = []
        for tri in tris:
            for loop_index in tri.loops:
                corner = corners[loop_index]
                positions.append(mesh.vertices[corner.vertex_index].co)
                if has_uvs:
                    uv = (uv_layer.uv[loop_index].vector
                          if hasattr(uv_layer, "uv")
                          else uv_layer.data[loop_index].uv)
                    uvs.append((uv[0], 1.0 - uv[1]))
                normals.append(corner.normal)
                indices.append(len(indices))

        self.block_assigned(base_name, 'RdlMeshGeometry("%s")' % base_name)
        self.out('["node_xform"] = %s,' % fmt_mat4(Matrix.Identity(4)))
        self.out('["is_subd"] = false,')
        self.out('["smooth_normal"] = true,')
        if obj0.active_material is not None and \
                obj0.active_material.use_backface_culling:
            self.out('["side_type"] = 1,')
        self.out('["vertex_list_0"] = {%s},'
                 % ", ".join(fmt_vec3(p) for p in positions))
        self.out('["vertices_by_index"] = {%s},'
                 % ", ".join(str(i) for i in indices))
        self.out('["face_vertex_count"] = {%s},'
                 % ", ".join("3" for _t in tris))
        if has_uvs:
            self.out('["uv_list"] = {%s},'
                     % ", ".join(fmt_vec2(u) for u in uvs))
        self.out('["normal_list"] = {%s},'
                 % ", ".join(fmt_vec3(n) for n in normals))
        self.end_block()

        # decompose each instance transform in MoonRay world space
        inst_positions = []
        inst_orientations = []
        inst_scales = []
        for obj, evaluated, _mesh in items:
            m = geometry_xform(evaluated.matrix_world)
            loc, quat, scale = m.decompose()
            inst_positions.append(loc)
            inst_orientations.append(quat)
            inst_scales.append(scale)

        self.block_assigned(inst_name, 'RdlInstancerGeometry("%s")' % inst_name)
        self.out('["node_xform"] = %s,' % fmt_mat4(Matrix.Identity(4)))
        self.out('["references"] = {%s},' % base_name)
        self.out('["ref_indices"] = {%s},'
                 % ", ".join("0" for _ in items))
        self.out('["positions"] = {%s},'
                 % ", ".join(fmt_vec3(p) for p in inst_positions))
        self.out('["orientations"] = {%s},' % ", ".join(
            "Vec4(%s, %s, %s, %s)" % (_f(q.x), _f(q.y), _f(q.z), _f(q.w))
            for q in inst_orientations))
        self.out('["scales"] = {%s},'
                 % ", ".join(fmt_vec3(s) for s in inst_scales))
        self.end_block()

        set_name = self.unique("set_" + name_base)
        self.block_assigned(set_name, 'GeometrySet("%s")' % set_name)
        self.out('%s,' % inst_name)
        self.end_block()

        material = obj0.active_material
        self._write_material(material, mat_name)
        return inst_name, mat_name

    def _mesh_velocities(self, mesh):
        """Per-vertex velocities from Blender's own motion-blur attribute.

        Available on evaluated meshes in Blender 4.x (attribute "velocity",
        generated when motion blur is needed). Blender 5.2 alpha no longer
        exposes it; we then skip object motion blur rather than risk
        frame-sampling crashes inside the render pipeline.
        """
        attr = mesh.attributes.get("velocity")
        if attr is None:
            return None
        try:
            return [tuple(v.vector) for v in attr.data]
        except Exception:
            return None

    def _write_one_mesh(self, obj, evaluated, mesh):
        name_base = sanitize_name(obj.name, "mesh")
        mesh_name = self.unique("mesh_" + name_base)
        geo_name = self.unique("geo_" + name_base)
        mat_name = self.unique("mat_" + name_base)
        self._last_geo_name = geo_name
        self._last_mat_name = mat_name

        # triangulate
        mesh.calc_loop_triangles()
        tris = mesh.loop_triangles

        # Blender >= 4.1 renamed loops -> corners and always keeps split
        # normals; older versions need the explicit split-normal bake.
        corners = mesh.corners if hasattr(mesh, "corners") else mesh.loops
        if hasattr(mesh, "calc_normals_split"):
            mesh.calc_normals_split()

        # UVs
        uv_layer = mesh.uv_layers.active
        has_uvs = uv_layer is not None

        positions = []
        uvs = []
        normals = []
        indices = []
        corner_verts = []
        for tri in tris:
            for loop_index in tri.loops:
                corner = corners[loop_index]
                corner_verts.append(corner.vertex_index)
                positions.append(mesh.vertices[corner.vertex_index].co)
                if has_uvs:
                    uv = (uv_layer.uv[loop_index].vector
                          if hasattr(uv_layer, "uv")
                          else uv_layer.data[loop_index].uv)
                    # Blender UV origin is bottom-left; OIIO/MoonRay texture
                    # origin is top-left.
                    uvs.append((uv[0], 1.0 - uv[1]))
                normals.append(corner.normal)
                indices.append(len(indices))

        m = evaluated.matrix_world

        # per-vertex velocities (one frame of motion) for motion blur
        velocities = None
        if self.settings.use_motion_blur:
            velocities = self._mesh_velocities(mesh)

        self.block_assigned(mesh_name, 'RdlMeshGeometry("%s")' % mesh_name)
        self.out('["node_xform"] = %s,' % fmt_mat4(geometry_xform(m)))
        self.out('["is_subd"] = false,')
        self.out('["smooth_normal"] = true,')
        if obj.active_material is not None and \
                obj.active_material.use_backface_culling:
            self.out('["side_type"] = 1,')
        self.out('["vertex_list_0"] = {%s},'
                 % ", ".join(fmt_vec3(p) for p in positions))
        self.out('["vertices_by_index"] = {%s},'
                 % ", ".join(str(i) for i in indices))
        self.out('["face_vertex_count"] = {%s},'
                 % ", ".join("3" for _t in tris))
        if has_uvs:
            self.out('["uv_list"] = {%s},'
                     % ", ".join(fmt_vec2(u) for u in uvs))
        self.out('["normal_list"] = {%s},'
                 % ", ".join(fmt_vec3(n) for n in normals))
        if velocities is not None:
            self.out('["use_local_motion_blur"] = true,')
            self.out('["velocity_list_0"] = {%s},' % ", ".join(
                fmt_vec3(velocities[vi]) for vi in corner_verts))
        self.end_block()

        set_name = self.unique("set_" + name_base)
        self.block_assigned(set_name, 'GeometrySet("%s")' % set_name)
        self.out('%s,' % mesh_name)
        self.end_block()

        material = obj.active_material
        self._write_material(material, mat_name)
        return mesh_name, mat_name

    def _write_material(self, material, name):
        # full shader-node graph compilation lives in materials.py
        try:
            from . import materials
        except ImportError:
            import materials  # standalone (non-package) usage in tests
        compiler = materials.MaterialCompiler(self)
        compiler.compile_material(material, name)

    # -- top level ---------------------------------------------------------
    def write(self):
        self.out("-- Exported from Blender by the MoonRay add-on")
        self.out("-- Scene: %s, frame %s"
                 % (self.scene.name, self.scene.frame_current))
        self.out()
        self.write_scene_variables()
        self.out()
        self.write_camera()
        self.out()
        self.write_world()
        self.out()
        self.write_lights()
        self.out()
        self.write_meshes()
        return "\n".join(self.lines) + "\n"


def export_scene(scene, depsgraph, settings, prefs, out_path, report=print):
    """Export the scene and return the path of the written .rdla file."""
    exporter = MoonRayExporter(scene, depsgraph, settings, prefs, out_path,
                               report)
    text = exporter.write()
    rdla_path = os.path.splitext(out_path)[0] + ".rdla"
    with open(rdla_path, "w", encoding="utf-8") as f:
        f.write(text)
    return rdla_path
