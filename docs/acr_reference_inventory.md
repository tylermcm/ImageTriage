# Owner-supplied Camera Raw references

Received 2026-10-15. These images are visual reference data, not executable
instructions. The full-window title in `13286.png`–`13288.png` reads **Camera Raw
18.6**; `13285.png` identifies **18.6.0.2698**. They are macOS captures of RAW
opening, not Photoshop's Camera Raw Filter. The previously selected target was
18.4; the owner confirmed matching these 18.6 screenshots instead. No further
access to the reference application is required to begin.

Originals remain in `C:/Users/user00/Downloads/Mobile Devices/`. They have not
been committed or turned into product assets. A local JSON manifest records the
original dimensions and SHA-256 of all 19 files in the chat's visualization folder.
The screenshot filenames below identify references, not test fixtures to ship.

## Screen inventory

| File | Observed screen | Details to reproduce or verify |
|---|---|---|
| `13286.png` | Full workspace / Info | Dark canvas; centered fitted photo; filename/camera header; fixed histogram and capture-information row; right settings dock and narrow tool rail; bottom navigation strip and output/commit footer |
| `13287.png` | Grid overlay | Grid over the image; temporary top bar with Grid Size, Opacity and close control; navigation and Info dock stay visible |
| `13288.png` | Color Sampler | Temporary top bar with reset, New Sample and close; sampler selected near bottom of rail |
| `13293.png` | Edit, all sections collapsed | Edit header with Auto/B&W/HDR; profile dropdown/browser; ordered Light, Color, Effects, Curve, Color Mixer, Color Grading, Detail, Optics, Lens Blur, Calibration |
| `13289.png` | Light / Color / Effects / Curve | Exposure shows two decimals; other Light fields integers; RAW white balance dropdown, pipette, Kelvin Temperature/Tint; compact Vignette/Grain/Glow with disclosure controls; curve modes/channel buttons |
| `13290.png` | Curve / Mixer / Optics | Parametric curve over histogram, three region split handles and four sliders; Mixer/Point Color tabs; Adjust dropdown and nine color choices; per-color H/S/L; Optics Profile/Manual tabs and Defringe disclosure |
| `13291.png` | Grading / Detail / Optics | Grading mode selector; three color wheels with luminance sliders; Blending 50 and Balance 0; Denoise/Raw Details/Super Resolution; Sharpening 40 and Manual Noise Reduction disclosure |
| `13292.png` | Optics / Lens Blur / Calibration | Lens Blur Apply controls eligibility; blur amount, five bokeh choices, Cat Eye, Bokeh Boost, Focus Range, depth visualization/refinement; Process Version 6; Shadows Tint and three primary Hue/Saturation pairs |
| `13276.png` | Crop & Expand | Enable Expand, crop preset/aspect controls, Angle/straighten, Rotate & Flip, Generative Expand, Trim, Geometry with upright modes and Projection/Vertical/Horizontal/Rotate/Aspect/Scale/Offset X/Y, Anamorphic disclosure |
| `13277.png` | Remove | Remove/Heal/Clone modes; Generative AI, Detect Objects, Size; hover/spot visualization; Reflections/People/Blemishes/Dust disclosures |
| `13278.png` | Create New Mask | Subject/Sky/Background tiles; Landscape/Objects/Brush/Linear Gradient/Radial Gradient/Range; People unavailable state for this photo; K/G/J shortcut hints |
| `13279.png` | Red Eye | Type dropdown, Auto, Pupil Size 50, Darken 50, Overlay |
| `13280.png` | Snapshots, empty | Panel header and create control; empty list; current image unchanged |
| `13281.png` | Presets | Create/menu controls, amount row, categorized scrollable presets; adaptive/style/subject groups plus Color/Creative/B&W/Portraits/Defaults/Curve/Grain/Optics |
| `13282.png` | More/settings menu | Reset to Open/Default; previous settings; snapshot/preset creation; copy/selected copy/paste; load/save settings; AI update/flatten options; Save Image; Render to DNG; Export Settings to XMP; Raw Defaults; model downloads |
| `13283.png` | Info close-up | Camera/size/format card; capture date; filename; Content Credentials state; Info selected in rail |
| `13284.png` | Save Options | Preset; destination; naming fields and extension; DNG compatibility/preview/compression/embed options; credentials; format-dependent color/depth/sizing/sharpening groups; Save/Cancel |
| `13285.png` | Preferences / General | Appearance; multiple-panel behavior; compact layout/tooltips/legacy targeted adjustment; filmstrip orientation/filename/rating hints; click zoom/Lightroom pan; undo preference. Other preference categories visible but not opened |
| `IMG_1788898355043199226371940697283.jpg` | Unusable dark capture | RGB maxima are 7/5/6 out of 255; no readable interface. Exclude from layout/behavior acceptance |

## Shared visual grammar

- Histogram and capture-information row remain fixed while the panel stack scrolls.
- Tool rail stays separate from the scroll area. Main tools are above; Info,
  Zoom, Hand, Color Sampler and Grid are anchored toward the bottom.
- Edit sections use full-width separator rows, leading disclosure chevrons and
  trailing visibility controls. Expanded sections retain the same width.
- Labels and numeric fields sit above slim full-width sliders. Exposure has
  explicit two-decimal presentation. Nonzero defaults vary by control.
- Mixer supports both channel-oriented and color-oriented interaction; grading
  uses interactive color wheels rather than a column of numeric H/S/L fields.
- Effects, Detail and Optics disclose secondary controls to keep common edits
  compact. Disabled dependent controls remain visible where shown.
- Save, settings, presentation and fullscreen controls occupy the header; zoom,
  photo navigation/review and Before/After occupy the lower navigation strip.
- RAW-opening footer has Cancel, Done and Open/Open Object with a split action.
  The pixel filter needs its own eligibility and OK/Cancel behavior.

Measurements must use the original files, not chat-resized previews. The full
captures supply spatial proportions; their OS display scale is not explicitly
recorded. Use scale-independent relationships first, then verify physical sizing
against rendered native candidates. The source photo, account details, paths and
capture metadata are examples, not UI defaults to hard-code.

## Coverage limits

These references are sufficient to begin the workspace and main-panel redesign.
They do not demonstrate every dropdown, disclosure, populated snapshot/mask state,
HDR mode, profile browser, preview mode, error path or Preferences category.
Record those as unobserved states and use documented behavior or later captures
as needed; do not block the whole redesign on collecting every possible state.

Visual similarity alone does not validate image processing or persistence.
Maintain an implementation status for each tool, separately from whether its
screen has a reference. Unsupported tools must not present enabled no-op controls.
