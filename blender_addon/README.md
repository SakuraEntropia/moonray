# MoonRay for Blender

Blender integration for the [MoonRay](https://github.com/OpenMoonRay/openmoonray)
production path tracer (DreamWorks / Academy Software Foundation).

The add-on registers **MoonRay** as a render engine in Blender:

1. exports the Blender scene to MoonRay's RDLA scene format
   (meshes, UVs, normals, materials, lights, camera, world),
2. runs the `moonray` command-line renderer,
3. loads the result back into the Render Result (F12 / animation rendering),
4. optionally denoises with MoonRay's OIDN `denoise` tool.

## Requirements

- macOS (Apple Silicon) with a working MoonRay installation built with the
  official `macos-release` CMake preset (see `openmoonray/building/macOS`).
  Linux installations (Rocky Linux 9) should work as well; the add-on itself
  only shells out to the `moonray` binary.
- Blender 4.0 or newer.

## Installation

Set the MoonRay installation path in
*Edit → Preferences → Add-ons → MoonRay Render*:

- **MoonRay Installation** — the directory containing `bin/moonray`
  (e.g. `/Users/<you>/Documents/wave-tracer/installs/openmoonray`)
- **Dependencies Install Root** — the directory containing the third-party
  `lib/` used by MoonRay (e.g. `/Users/<you>/Documents/wave-tracer/installs`)

The auto-detection default looks next to this add-on's source tree
(`<workspace>/../installs/openmoonray`).

## Usage

1. Switch the render engine to **MoonRay** in *Render Properties*.
2. Tune samples (MoonRay `pixel_samples` is the square root of the spp),
   threads, denoise, etc. in the *MoonRay* panel.
3. Press F12. The scene is exported to a temporary `.rdla`, rendered, and the
   EXR is loaded into the Render Result. Animation rendering (Ctrl+F12) is
   supported frame by frame.

## Supported Blender features

| Feature            | Status                                             |
|--------------------|----------------------------------------------------|
| Meshes (quads/ngons, triangulated) | ✔ with UVs and split normals           |
| Curves/surfaces/text (via to_mesh) | ✔                                  |
| Instancing (linked duplicates) | ✔ exported as RdlInstancerGeometry            |
| Principled BSDF    | base color (+ image texture), roughness, metallic, specular, transmission, emission, alpha |
| Point / Sun / Spot / Area lights | ✔ with energy-based intensity mapping   |
| World background   | constant color from the Background node         |
| Depth of field     | ✔ (camera DOF settings)                          |
| Motion blur, volumetrics, HDRI environments | not yet             |

## Notes

- MoonRay is Y-up while Blender is Z-up; the exporter applies the standard
  axis conversion (`x, z, -y`) to all transforms.
- Light intensities are converted from Blender watts to MoonRay radiance-ish
  units; use the global *Light Intensity Scale* in the add-on preferences to
  compensate for scene scale.
- Packed image textures (without a file on disk) fall back to the material's
  base color.
