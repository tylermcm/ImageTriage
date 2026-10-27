pragma Singleton
import QtQuick

// Shared interface state: which rail tab is open, which menu, which buttons are pinned, view options.
QtObject {
    property string tab: "lib"          // "" = side panel closed
    property bool insp: true
    property bool zen: false            // Zen mode: the side panel and the inspector put away, and put back when it ends
    property string zenTab: "lib"
    property bool zenInsp: true
    signal undoRequested()                  // the rail's Undo button: Main runs the Undo command
    property real panelW: 300               // widths the user dragged the side panel and the inspector to (Main clamps them to what fits)
    property real inspW: 352
    readonly property real panelMin: 260    // narrower than this the library rows, faces and PocketDrop cards would clip
    readonly property real panelMax: 300
    readonly property real inspMin: 300
    readonly property real inspMax: 352
    property string menu: ""            // "" or the id of the open popover
    property string filter: "all"
    property string dens: "m"
    property var railOrder: ["palette", "openFolder", "undo", "view", "funnel", "sort", "columns", "compare", "convert", "editor", "check", "accept", "reject", "tag", "newfolder", "pocketdrop", "eye", "keyboard"]   // the default order; the order the rail's pinned buttons and the floating bar's buttons are shown in (ids; DragReorder)
    property var barOrder: []
    property var destOrder: []            // the order of the four destinations at the top of the rail
    property int cols: 0                  // a fixed number of columns, 1 to 9; 0 lets the thumbnail size (dens) decide how many fit
    function setDens(d) { cols = 0; dens = d }
    property int minRate: 0
    property string fmt: "all"
    property string sort: "name"
    property bool showNames: true
    property bool showBadges: true
    property bool compare: false
    property int ai: 0                  // 0 idle, 1 analyzing, 2 ready
    property bool topAligned: false
    property bool settingsOpen: false
    property bool guideCovers: false         // the settings guide is fully in front of the settings window (which then steps out of the way)
    property string settingsSection: "general"
    property string settingsQuery: ""
    property string batchMode: ""          // "" closed, or "rename", "resize", "convert"
    property bool filtersOpen: false
    property bool handoffOpen: false
    property string txState: ""            // test hooks for the transfer cards: confirm | progress | done, with txKind and txP
    property string txKind: "copy"
    property real txP: 0.42
    property int txCount: 12
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
    // collections, faces, best-of, tag people (test hooks: collDlg, collDlgId, collDlgTab, collName, collMenu, collPicker, cldrop, peopleSel ...)
    property string collDlg: ""             // "" closed, "new" or "edit"
    property string collDlgId: ""
    property string collDlgTab: "icon"
    property string collName: ""            // test hook: name typed into the dialog
    property string collMenu: ""            // id of the collection whose menu is open ("" none)
    property bool collPicker: false
    property string collPickerQuery: ""
    property string cldrop: ""              // id of the collection row a drag is over
    property bool bestOfOpen: false
    property string boStrat: ""             // test hook: the strategy the best-of window starts on
    property int boN: 0                     // test hook: its proposal size
    property bool peopleOpen: false
    property string peopleFilter: "all"
    property bool peopleSingles: false
    property string peopleQuery: ""
    property string peopleSel: ""           // test hook: comma-separated ids to start selected
    property string faceQuery: ""
    property string vwScene: ""             // checks: open the viewer on row vwRow in the scene of the mockup's name (view, zoom, loupe, focus, collect, zen, opening, loading)
    property int vwRow: -1
    property int vwFh: 0
    property int requestRow: -1             // a window asks the grid to move to this row (Show in grid)
    property bool compact: false         // window <= 900px: narrow rail, panel and inspector float over the grid (the mockup's phone layout)
    property string topMenu: ""             // the open top-bar menu: file, review, view or ai ("" none; Help has its own, in `reference`)
    property real topMenuX: 0               // where it drops from
    property int topSub: -1                 // the row of that menu whose fly-out shows
    signal openFolderRequested()            // the rail's Open folder pin, Ctrl+O and the palette: Main.qml shows the folder dialog
    property real railPin: 40              // the size of the rail's pinned buttons (Rail.qml shrinks it when the window is short)
    property var pins: ({
        open: true, undo: true, view: true, filters: true, sort: true, columns: true, compare: true, convert: true, editor: true, palette: true,
        review: false, accept: false, reject: false, rename: false, preview: false, share: false, newfolder: false, shortcuts: false
    })
    property bool pinsLocked: false       // locked: the pinned buttons cannot be switched on or off in the "+" menu, and nothing on the rail can be dragged
    function openPalette(context) { paletteContext = context || "main"; paletteQuery = ""; paletteSel = 0; paletteOpen = true }
    function togglePin(name) { var p = Object.assign({}, pins); p[name] = !p[name]; pins = p; }
    function setMenu(name) { menu = (menu === name) ? "" : name; }
    // fractional pixel sizes (10.5, 11.5 ...) are not possible with pixelSize, so they go through pointSize at 96 dpi
    function pt(px) { return px * 0.75 }
    readonly property var tabs: ["lib", "faces", "coll", "drop"]
    readonly property var tabTitles: ({ lib: "Library", faces: "Faces", coll: "Collections", drop: "PocketDrop" })
}
