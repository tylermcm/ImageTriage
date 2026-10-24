# QML shell parity against the HTML mockup

`Main.dc.html` is the mockup. `harness.html` renders it in Chromium without the artifact runtime (a 60-line stand-in for `support.js`:
`{{path}}` bindings, `sc-for`, click/keydown handlers), so the same state can be rendered at any window size and compared with the QML shell
pixel for pixel.

```bash
# 1. serve the mockup with its 12 photos in ./img (p01.jpg .. p12.jpg, any 3:2 landscapes)
cd scripts/qml_parity && ln -s /path/to/12/photos img && python3 -m http.server 8765 &

# 2. reference render (needs playwright: npm i playwright)
node shot.js "http://localhost:8765/harness.html?itab=quality" ref.png 1440 900
#   query: light=1  tab=lib|faces|coll|drop|(empty)  insp=0  itab=info|quality|ai  sel=N  dens=s|m|l  menu=fReview|fView|fSort  align=aligned

# 3. the QML shell with the mockup's data, same state
python -m image_triage.qml_shell --mock /path/to/12/photos --grab qml.png --grab-wait 5 --size 1440x900 --ui "sel=1;inspTab=1"
#   --ui keys are UiState properties (tab, insp, dens, menu, ...) plus sel (grid row) and inspTab (0..2); --light for the light theme

# 4. overlay: side-by-side, 50% blend, 6x amplified difference, worst blocks
python scripts/qml_parity.py ref.png qml.png out --crop 1088,42,352,520 --zoom 2
node dump.js "http://localhost:8765/harness.html" ".fbar, .fbar *"      # exact boxes and computed styles of any selector
```

Mean absolute difference of the full window is about 3.5/255 in the dark theme; the remainder is text anti-aliasing (Qt vs Skia) and the
thumbnails, which come from Image Triage's own thumbnail cache rather than the browser's image scaler.

## Where the mockup's rendering is not its intent (replicated or not)

* `.app button{color:inherit;font:inherit}` outranks `.ibtn`, `.rt`, `.fb` and `.rb`, so the rail labels render at 13px regular and the icon
  buttons use the full text colour. **Replicated** (that is what the screenshots show).
* `.pop{left:0;bottom:...}` is defined after `.rpop{left:calc(100% + 10px)}`, so the rail's pinned-button menus collapse over the rail in the
  mockup. **Not replicated**: the QML menus open to the right of the button, as the CSS intended.
* (Fixed) At 1440 wide with the inspector open the bar's AI button used to overflow the glass; the size group now drops as soon as the bar would not fit (mockup and shell).
* At 900px and below the rail labels stay visible but clipped (`font-size:0` is overridden) and the floating bar ends up below the
  window (the main column is taller than the viewport). **Not replicated**: icon-only rail, bar visible.
