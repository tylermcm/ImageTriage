pragma Singleton
import QtQuick

// Shared interface state: which rail tab is open, which menu, which buttons are pinned, view options.
QtObject {
    property string tab: "lib"          // "" = side panel closed
    property bool insp: true
    property string menu: ""            // "" or the id of the open popover
    property string filter: "all"
    property string dens: "m"
    property int minRate: 0
    property string fmt: "all"
    property string sort: "name"
    property bool showNames: true
    property bool showBadges: true
    property bool compare: false
    property int ai: 0                  // 0 idle, 1 analyzing, 2 ready
    property bool topAligned: false
    property bool settingsOpen: false
    property string settingsSection: "general"
    property string settingsQuery: ""
    property string batchMode: ""          // "" closed, or "rename", "resize", "convert"
    property bool filtersOpen: false
    property real filtersScroll: 0          // test hook: where the filters page starts scrolled
    property bool paletteOpen: false
    property string paletteContext: "main"   // "main", "preview" or "zen": which commands are offered and what the card is called
    property string paletteQuery: ""
    property int paletteSel: 0               // highlighted row (test hook; the keyboard moves it)
    property int selRow: -1                  // the grid's current row (bound in Main), what "Selection" in PocketDrop's Library means
    // dragging a collection (or the selection) towards PocketDrop; pdFlow is the test hook that shows the mockup's still frames of it
    property bool pdDragging: false
    property var pdDragInfo: ({ key: "", title: "", sub: "", thumbs: ["", "", ""] })
    property real pdGhostX: 0
    property real pdGhostY: 0
    property bool pdSim: false
    property bool pdRailHot: false           // the dragged item is over the PocketDrop button (switches to its page after a moment)
    property string pdFlow: ""
    onPdFlowChanged: {
        if (pdFlow === "dragging") { pdDragInfo = { key: "col:album", title: "Album selects", sub: "84 photos \u00b7 612 MB", thumbs: ["", "", ""] }; pdSim = true; pdDragging = true }
        else { pdSim = false; pdDragging = false }
    }
    property bool compact: false         // window <= 900px: narrow rail, panel and inspector float over the grid (the mockup's phone layout)
    property var pins: ({
        open: true, undo: true, review: true, view: true, filters: true, accept: true, reject: true,
        sort: false, columns: false, rename: false, compare: false, preview: false, ladder: false, convert: false, share: false,
        editor: false, newfolder: false, palette: false, shortcuts: false
    })
    function openPalette(context) { paletteContext = context || "main"; paletteQuery = ""; paletteSel = 0; paletteOpen = true }
    function togglePin(name) { var p = Object.assign({}, pins); p[name] = !p[name]; pins = p; }
    function setMenu(name) { menu = (menu === name) ? "" : name; }
    // fractional pixel sizes (10.5, 11.5 ...) are not possible with pixelSize, so they go through pointSize at 96 dpi
    function pt(px) { return px * 0.75 }
    readonly property var tabs: ["lib", "faces", "coll", "drop"]
    readonly property var tabTitles: ({ lib: "Library", faces: "Faces", coll: "Collections", drop: "PocketDrop" })
}
