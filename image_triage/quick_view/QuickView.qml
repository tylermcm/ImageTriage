import QtQuick
import QtQuick.Window

// The quick viewer (quick_view/viewer.py): one photo from the file manager, at once. Only QtQuick, so it loads in a moment; the look
// follows the library's viewer (its dark stage and glass bars). In a window the bar sits above the photo; full screen hides it until the
// pointer moves. The photo fits the window (never enlarged); the wheel zooms about the
// pointer, a double-click swaps fit and 100%, a drag moves a zoomed photo. Left and Right (and Space, Backspace, the mouse's side buttons)
// go through the folder; Library (L) opens the full app on the photo, Edit (E) opens it there with the editor (loaded only then); F or F11 is full screen; Esc leaves full screen, then closes.
Window {
    id: win
    objectName: "quickViewWindow"
    // placed and shown by quick_view.place_window: on the monitor under the pointer (QML's Screen.desktopAvailable* span every
    // monitor, which made the window as wide as all of them)
    width: 1200; height: 800
    minimumWidth: 420; minimumHeight: 300
    visible: false
    color: cBase
    title: viewer.name + (viewer.count > 1 ? "  (" + (viewer.index + 1) + " of " + viewer.count + ")" : "") + " – Image Triage"

    readonly property color cBase: "#16171a"
    readonly property color cGlass: Qt.rgba(0.11, 0.115, 0.13, 0.86)
    readonly property color cBtn: "#2a2c30"
    readonly property color cHover: "#34363b"
    readonly property color cLine: "#3a3c40"
    readonly property color cFg: "#e9eaec"
    readonly property color cFg2: "#b8babe"
    readonly property color cFg3: "#8b8d92"
    readonly property color cAccent: "#59a0f9"
    readonly property bool full: visibility === Window.FullScreen
    // the photo: its own size (100%), and the picture last shown (kept while the next one decodes, so nothing flashes)
    readonly property real pw: Math.max(1, viewer.photoWidth)
    readonly property real ph: Math.max(1, viewer.photoHeight)
    property string shown: ""
    property real zoom: 0                      // 0: fit the window; otherwise the scale (1 = 100%)
    property real panX: 0
    property real panY: 0
    readonly property real fitScale: Math.min(1, stage.width / pw, stage.height / ph)
    readonly property real scale: zoom > 0 ? zoom : fitScale
    property bool framed: false
    readonly property bool photoReady: photo.status === Image.Ready && photo.source != ""
    property bool chrome: true                 // the bars: shown while the pointer moves, hidden after a pause
    Connections {
        target: viewer
        function onChanged() {
            if (viewer.source !== "" && viewer.source !== win.shown) {
                var same = photo.status === Image.Ready && win.shownPath === viewer.path
                win.shown = viewer.source
                win.shownPath = viewer.path
                if (!same) { win.zoom = 0; win.panX = 0; win.panY = 0 }
            }
        }
    }
    property string shownPath: ""
    onFrameSwapped: {
        if (!framed) { framed = true; viewer.firstFrame() }
        if (photo.status === Image.Ready && photo.source != "") viewer.photoFrame()
    }
    function toggleFull() { visibility = full ? Window.Windowed : Window.FullScreen }
    function poke() { chrome = true; hide.restart() }
    function clampPan() {
        var w = pw * scale, h = ph * scale
        panX = w <= stage.width ? 0 : Math.max(-(w - stage.width) / 2, Math.min((w - stage.width) / 2, panX))
        panY = h <= stage.height ? 0 : Math.max(-(h - stage.height) / 2, Math.min((h - stage.height) / 2, panY))
    }
    // zoom to `next` keeping the photo's point under (x, y) where it is
    function zoomAt(next, x, y) {
        next = Math.max(fitScale, Math.min(8, next))
        var before = scale
        var cx = x - stage.width / 2 - panX, cy = y - stage.height / 2 - panY
        zoom = next <= fitScale + 0.0001 ? 0 : next
        panX -= cx * (scale / before - 1)
        panY -= cy * (scale / before - 1)
        clampPan()
        if (pw * scale > photo.sourceSize.width + 1) viewer.wantFull()
    }
    Timer { id: hide; interval: 2200; onTriggered: if (!barHover.hovered) win.chrome = false }

    Item {
        id: stage
        anchors.fill: parent
        anchors.topMargin: win.full ? 0 : bar.height          // in a window the bar has its own strip; full screen gives the photo all of it
        focus: true
        clip: true
        Image {
            id: photo
            objectName: "quickViewPhoto"
            source: win.shown
            asynchronous: true; cache: false; smooth: true; mipmap: true
            fillMode: Image.Stretch
            width: win.pw * win.scale; height: win.ph * win.scale
            x: (stage.width - width) / 2 + win.panX
            y: (stage.height - height) / 2 + win.panY
            visible: win.shown !== ""
        }
        Text {
            anchors.centerIn: parent; width: Math.min(parent.width - 80, 520); horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
            visible: viewer.error !== ""; text: viewer.error; color: win.cFg2; font.pixelSize: 15
        }
        Text {
            anchors.centerIn: parent; visible: viewer.loading && win.shown === "" && slow.done; text: "Opening " + viewer.name + "…"; color: win.cFg3; font.pixelSize: 14
            Timer { id: slow; property bool done: false; interval: 300; running: true; onTriggered: done = true }
        }
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: (e) => win.zoomAt(win.scale * Math.pow(1.0015, e.angleDelta.y), e.x, e.y)
        }
        DragHandler {
            target: null; enabled: win.zoom > 0
            property real sx: 0; property real sy: 0
            onActiveChanged: if (active) { sx = win.panX; sy = win.panY }
            onTranslationChanged: { win.panX = sx + translation.x; win.panY = sy + translation.y; win.clampPan() }
        }
        TapHandler {
            onDoubleTapped: (point) => { if (win.zoom > 0) { win.zoom = 0; win.panX = 0; win.panY = 0 } else win.zoomAt(1, point.position.x, point.position.y) }
        }
        TapHandler { acceptedButtons: Qt.BackButton; onTapped: viewer.go(-1) }
        TapHandler { acceptedButtons: Qt.ForwardButton; onTapped: viewer.go(1) }
        HoverHandler { onPointChanged: win.poke() }
        Keys.onPressed: (e) => {
            var k = e.key
            if (k === Qt.Key_Right || k === Qt.Key_PageDown || k === Qt.Key_Space) viewer.go(1)
            else if (k === Qt.Key_Left || k === Qt.Key_PageUp || k === Qt.Key_Backspace) viewer.go(-1)
            else if (k === Qt.Key_Home) viewer.goTo(0)
            else if (k === Qt.Key_End) viewer.goTo(viewer.count - 1)
            else if (k === Qt.Key_F || k === Qt.Key_F11 || k === Qt.Key_Return || k === Qt.Key_Enter) win.toggleFull()
            else if (k === Qt.Key_Escape) { if (win.full) win.toggleFull(); else win.close() }
            else if (k === Qt.Key_L) viewer.openLibrary()
            else if (k === Qt.Key_E) viewer.openEditor()
            else if (k === Qt.Key_Plus || k === Qt.Key_Equal) win.zoomAt(win.scale * 1.25, stage.width / 2, stage.height / 2)
            else if (k === Qt.Key_Minus) win.zoomAt(win.scale / 1.25, stage.width / 2, stage.height / 2)
            else if (k === Qt.Key_0 || k === Qt.Key_Z) { if (win.zoom > 0) { win.zoom = 0; win.panX = 0; win.panY = 0 } else win.zoomAt(1, stage.width / 2, stage.height / 2) }
            else return
            e.accepted = true
        }
    }

    // ---- the bar: the photo's name and place, its zoom, and the way to the library -------------------------------------------------------
    Rectangle {
        id: bar
        objectName: "quickViewBar"
        width: parent.width; height: 52
        color: win.cGlass
        opacity: !win.full || win.chrome || barHover.hovered ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 180 } }
        HoverHandler { id: barHover }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.rgba(1, 1, 1, 0.06) }
        Column {
            x: 18; anchors.verticalCenter: parent.verticalCenter; width: parent.width - right.width - 54; spacing: 1
            Text { width: parent.width; elide: Text.ElideMiddle; text: viewer.name; color: win.cFg; font.pixelSize: 14; font.weight: Font.DemiBold }
            Text {
                width: parent.width; elide: Text.ElideRight; color: win.cFg3; font.pixelSize: 12
                text: (viewer.count > 1 ? (viewer.index + 1) + " of " + viewer.count + "  ·  " : "") + viewer.folder
                      + (viewer.photoWidth > 0 ? "  ·  " + viewer.photoWidth + " × " + viewer.photoHeight : "")
                      + (viewer.kind === "edited" ? "  ·  with your edits" : viewer.kind === "embedded" ? "  ·  camera preview" : "")
            }
        }
        Row {
            id: right
            anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; spacing: 8
            Text { anchors.verticalCenter: parent.verticalCenter; text: Math.round(win.scale * 100) + "%"; color: win.cFg3; font.pixelSize: 12; rightPadding: 6 }
            Btn { label: win.full ? "Exit full screen" : "Full screen"; onClicked: win.toggleFull() }
            Btn {
                objectName: "quickViewEdit"
                visible: viewer.editable
                label: viewer.library === "starting" && viewer.goingTo === "edit" ? "Opening the editor…" : "Edit"
                onClicked: viewer.openEditor()
            }
            Btn {
                id: libBtn
                objectName: "quickViewLibrary"
                primary: true
                label: viewer.library === "starting" && viewer.goingTo === "library" ? "Opening the library…"
                     : viewer.library === "failed" ? "Did not open: try again" : "Open in Library"
                onClicked: viewer.openLibrary()
            }
        }
    }

    // ---- the end of the folder: a toast, not a question ------------------------------------------------------------------------------------
    Connections { target: viewer; function onEdge(which) { toast.say(which === "end" ? "Last photo in the folder" : "First photo in the folder") } }
    Rectangle {
        id: toast
        objectName: "quickViewToast"
        property string text: ""
        function say(t) { text = t; opacity = 1; toastTimer.restart() }
        anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 40
        width: toastText.implicitWidth + 36; height: 38; radius: 19
        color: win.cGlass; border.color: win.cLine
        opacity: 0; visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 220 } }
        Text { id: toastText; anchors.centerIn: parent; text: toast.text; color: win.cFg; font.pixelSize: 13; font.weight: Font.Medium }
        Timer { id: toastTimer; interval: 1400; onTriggered: toast.opacity = 0 }
    }

    // ---- previous and next, at the sides ----------------------------------------------------------------------------------------------------
    Side { anchors.left: parent.left; step: -1; shown: viewer.index > 0 || !viewer.listed }
    Side { anchors.right: parent.right; step: 1; shown: viewer.index < viewer.count - 1 || !viewer.listed }

    component Btn: Rectangle {
        id: b
        property string label
        property bool primary: false
        signal clicked()
        height: 32; radius: 9; width: t.implicitWidth + 28
        color: primary ? (h.hovered ? Qt.lighter(win.cAccent, 1.08) : win.cAccent) : (h.hovered ? win.cHover : win.cBtn)
        border.width: primary ? 0 : 1; border.color: win.cLine
        Text { id: t; anchors.centerIn: parent; text: b.label; color: b.primary ? "#0e1624" : win.cFg; font.pixelSize: 13; font.weight: Font.Medium }
        HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: b.clicked() }
        Accessible.role: Accessible.Button; Accessible.name: b.label
    }
    component Side: Item {
        id: side
        property int step
        property bool shown
        width: 84; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.topMargin: 52
        visible: shown
        HoverHandler { id: sh; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: viewer.go(side.step) }
        Rectangle {
            anchors.centerIn: parent; width: 44; height: 44; radius: 22; color: win.cGlass; border.color: win.cLine
            opacity: sh.hovered ? 1 : (win.chrome ? 0.55 : 0)
            Behavior on opacity { NumberAnimation { duration: 160 } }
            Text { anchors.centerIn: parent; anchors.verticalCenterOffset: -2; text: side.step < 0 ? "‹" : "›"; color: win.cFg; font.pixelSize: 30 }
        }
    }
}
