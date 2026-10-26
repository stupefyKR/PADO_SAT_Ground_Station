# Earth image source

`earth-blue-marble-october.jpg` is NASA Earth Observatory's **Blue Marble: Next Generation with Topography and Bathymetry**, global October 2004 image (5400 × 2700 JPEG). Original:

https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/base-topography-bathymetry/

Direct original file:

https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-topography-bathymetry/october/world.topo.bathy.200410.3x5400x2700.jpg

Credit: **NASA Earth Observatory**. The reference tracker credits Esri, Maxar and Earthstar Geographics for its map imagery. Those third-party map tiles are not part of this repository. The NASA texture is kept locally so the authenticated portal does not request a third-party map service while displaying a mission orbit.

## PADO-SAT mission badge

`pado-sat-badge-ec9020cb.png` is the mission badge supplied by the project team. The PNG bytes are preserved without redrawing or cropping. Its SHA-256 is `ec9020cbb16ce13101b8c7912764ca9c86f25fcafdfa72fe172385f898428150`. The content-hashed filename permits private browser caching of this public image while login pages, authenticated CAD, and API responses remain `no-store`.

## PADO-SAT CAD surface captures

The team supplied `OneDrive_2026-10-26.zip` (SHA-256 `5ea224068d9650be27c313b4f7cfdc43a3a26c661ed679aa26f79b11ce0a861a`). The four PNGs below retain the original bytes, resolution (1593 × 779), lighting and CAD annotations. They are local assets, so the attitude viewer needs no external image service.

| Asset | Original capture | SHA-256 |
|---|---|---|
| `pado-sat-surface-front.png` | `disploy_-x.png` | `87934965753856afa2dda0b3097a9f43e32e56f2ac74504883fb82dd62660175` |
| `pado-sat-surface-rear.png` | `disploy_-x2.png` | `09690fef13604cf7006895c6bcd240a79fc2ac55170a3b66cf9781f4a224006b` |
| `pado-sat-surface-top.png` | `disploy_+x.png` | `126f53766a8943c90e5409af89f44592d9fb4d876b401b4da00481998bd5bdf8` |
| `pado-sat-surface-end.png` | `disploy_+x2.png` | `755400852ddf54bbf73b9b78d597317ed3b81c1eba1c102fea47d69a3aad2a7e` |

`pado-sat-appearance.json` records 10 manually selected planar surface correspondences in BODY millimetres. Each `image_quad` gives normalized source-image coordinates in the same order as its four 3D `corners`; these are projected back onto individual wing, bus, side or end surfaces rather than displaying a whole satellite capture on one face. The current subdivision budget is 408 triangles.

The antenna assembly is at the STEP model's −Z end and the circular aperture is at +Z. Source filenames such as `+x`/`-x` describe capture viewpoints and are not used as BODY face labels. Wing ±Y and long-side ±Y assignments are inferred from the deployed-wing and end landmarks; the captures do not include an authoritative body-axis overlay. The image lighting and small perspective/occlusion details are baked into the captures, so these textures reproduce their visible appearance rather than CAD-authored material properties. Replacing them with orthographic surface renders or a CAD UV export can remove those approximations later.

The long-side gold board patches sit just outside the outer board planes at BODY Y = ±65.5 mm, with X approximately ±41.3 mm. They cover the outer stowed boards shown in the captures rather than the narrower structural bus walls at Y = ±50 mm.
