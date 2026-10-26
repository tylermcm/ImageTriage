import QtQuick
import QtQuick.Effects
import QtQuick.Shapes

// The popout: one photo at full size over the whole window, a floating action bar and a full-width filmstrip that keeps the photo you are on in
// the middle (mockup: scripts/qml_parity/viewer.dc.html). The host is viewer_host.py (`popout`); the grid, the rail and the menus open it with
// popout.open(row). It is always dark, like a light table, whatever the theme is; only the accent follows the theme.
FocusScope {
    id: v
    objectName: "viewer"
    anchors.fill: parent
    z: 90
    readonly property bool shown: popout.qml === true && popout.isOpen
    visible: opacity > 0.01
    opacity: shown ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    onShownChanged: { if (shown) { zen = false; zoomed = false; loupe = false; collOpen = false; forceActiveFocus() } else { grip.dragging = false } }

    // ---- state ----
    property bool zen: false
    property bool zoomed: false
    property bool loupe: false
    property bool collOpen: false
    property bool opening: false          // the "Opening in PhotoCraft" screen (nothing starts it yet: the editor is not connected)
    property bool slow: false             // the loading look held on (checks only)
    readonly property int row: popout.row
    readonly property int fh: popout.stripHeight
    readonly property var cur: { if (!photos) return ({}); void photos.revision; return row >= 0 ? photos.get(row) : ({}) }
    readonly property string curPath: cur.path || ""
    readonly property string curDec: cur.decision || ""
    readonly property int curRating: cur.rating || 0
    readonly property var info: popout.info
    readonly property var flag: { if (!cull) return ({}); void cull.view; if (UiState.vwScene === "flagged") return ({ label: "Blurry", color: "#e0b04a" }); return row >= 0 ? popout.flagFor(row) : ({}) }
    readonly property bool loading: slow || popout.loading
    readonly property real dpr: Screen.devicePixelRatio
    readonly property int filmCenterRow: film.indexAt(film.contentX + film.width / 2, film.height / 2)     // which thumbnail is in the middle of the strip

    // ---- the viewer's own palette (dark in both themes) ----
    readonly property color cStage: "#030405"
    readonly property color cGlass: Qt.rgba(6 / 255, 7 / 255, 10 / 255, 0.74)
    readonly property color cS2: "#191a1d"
    readonly property color cLine: "#2a2b2e"
    readonly property color cLineSoft: "#1e1f22"
    readonly property color cFg: "#e3e3e4"
    readonly property color cFg2: "#a4a5a6"
    readonly property color cFg3: "#8d8e8f"
    readonly property color cBtn: "#1c1d20"
    readonly property color cBtnHover: "#242528"

    function decColor(d) { if (!theme) return "transparent"; return d === "win" ? theme.win : d === "keep" ? theme.keep : d === "rej" ? theme.rej : d === "rev" ? theme.rev : "transparent" }
    signal moveRequested(int row)
    function refocus() { if (shown) forceActiveFocus() }
    function close() { popout.close() }
    function go(r) { zoomed = false; loupe = false; popout.go(r) }
    function step(n) { go(popout.row + n) }
    function decide(d) { popout.decide(row, d) }
    function editor() { if (popout.canEdit && UiState.vwScene !== "noedit") popout.notice("The editor isn’t connected to this window yet") }
    function toggleZoom() { zoomed = !zoomed; if (!zoomed) loupe = false }
    function toggleLoupe() { loupe = !loupe; zoomed = loupe }

    Keys.onPressed: (e) => {
        if (!opening && (settings.keyIs("accept_selection", e.key, e.modifiers) || settings.keyIs("keep_at_cursor", e.key, e.modifiers) || settings.keyIs("reject_selection", e.key, e.modifiers))) {
            decide(settings.keyIs("accept_selection", e.key, e.modifiers) ? "win" : settings.keyIs("keep_at_cursor", e.key, e.modifiers) ? "keep" : "rej"); e.accepted = true; return
        }
        if (e.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) {
            if (e.key === Qt.Key_Z && (e.modifiers & Qt.ControlModifier)) { popout.undo(); e.accepted = true }
            return
        }
        var k = e.key
        if (opening) { if (k === Qt.Key_Escape) opening = false; e.accepted = true; return }
        e.accepted = true
        if (k === Qt.Key_Escape) { if (collOpen) collOpen = false; else close() }
        else if (k === Qt.Key_Space) close()
        else if (k === Qt.Key_Left || k === Qt.Key_A || k === Qt.Key_Up || k === Qt.Key_PageUp) step(-1)
        else if (k === Qt.Key_Right || k === Qt.Key_D || k === Qt.Key_Down || k === Qt.Key_PageDown) step(1)
        else if (k === Qt.Key_Home) go(0)
        else if (k === Qt.Key_End) go(photos.rowCount() - 1)
        else if (k === Qt.Key_U) { if (curDec !== "") decide(curDec) }
        else if (k >= Qt.Key_1 && k <= Qt.Key_5) popout.rate(row, k - Qt.Key_0)
        else if (k === Qt.Key_Z) toggleZoom()
        else if (k === Qt.Key_0) { zoomed = false; loupe = false }
        else if (k === Qt.Key_L) toggleLoupe()
        else if (k === Qt.Key_F) { if (e.modifiers & Qt.ShiftModifier) popout.cycleAssistColor(); else popout.toggleAssist() }
        else if (k === Qt.Key_H) zen = !zen
        else if (k === Qt.Key_B || k === Qt.Key_G) collOpen = !collOpen
        else if (k === Qt.Key_M) moveRequested(row)
        else if (k === Qt.Key_T) popout.notice("Tagging isn’t connected to this window yet")
        else if (k === Qt.Key_Delete) popout.delete(row)
        else if (k === Qt.Key_E) editor()
        else e.accepted = false
    }

    // test hook: --ui "vwScene=zoom" opens the viewer on the photo `vwRow` and sets the scene the mockup calls by that name
    Connections {
        target: UiState
        function onVwSceneChanged() { v.runScene() }
    }
    property bool scenePending: false
    Connections { target: photos; function onCountsChanged() { if (v.scenePending && photos.rowCount() > 0) v.runScene() } }
    function runScene() {
        var s = UiState.vwScene
        if (s === "") return
        if (photos.rowCount() === 0) { scenePending = true; return }    // the sample photos are read after the startup settings are applied
        scenePending = false
        popout.open(UiState.vwRow >= 0 ? UiState.vwRow : 1)
        if (UiState.vwFh > 0) popout.setStripHeight(UiState.vwFh)
        zen = s === "zen"; zoomed = s === "zoom" || s === "loupe"; loupe = s === "loupe"; collOpen = s === "collect"
        opening = s === "opening"; slow = s === "loading"
        if (s === "focus" && !popout.assistOn) popout.toggleAssist()
    }

    // ---- the stage ----
    Rectangle { anchors.fill: parent; color: v.cStage }
    Item {
        id: stage
        anchors.fill: parent
        anchors.leftMargin: v.zen ? 16 : 24; anchors.rightMargin: v.zen ? 16 : 24
        anchors.topMargin: v.zen ? 16 : 64; anchors.bottomMargin: v.zen ? 16 : v.fh + 88
        Behavior on anchors.leftMargin { NumberAnimation { duration: 350; easing.type: Easing.OutCubic } }
        Behavior on anchors.rightMargin { NumberAnimation { duration: 350; easing.type: Easing.OutCubic } }
        Behavior on anchors.topMargin { NumberAnimation { duration: 350; easing.type: Easing.OutCubic } }
        Behavior on anchors.bottomMargin { enabled: !grip.dragging; NumberAnimation { duration: 350; easing.type: Easing.OutCubic } }
        clip: true

        Flickable {
            id: pan
            anchors.fill: parent
            readonly property real nw: Math.max(1, popout.pixelWidth) / v.dpr
            readonly property real nh: Math.max(1, popout.pixelHeight) / v.dpr
            contentWidth: v.zoomed ? Math.max(width, nw) : width
            contentHeight: v.zoomed ? Math.max(height, nh) : height
            boundsBehavior: Flickable.StopAtBounds
            interactive: v.zoomed && !v.loupe
            clip: true
            onContentWidthChanged: if (v.zoomed) centerOn(0.5, 0.5)
            function centerOn(fx, fy) { contentX = Math.max(0, Math.min(contentWidth - width, fx * contentWidth - width / 2)); contentY = Math.max(0, Math.min(contentHeight - height, fy * contentHeight - height / 2)) }
            Item {
                id: canvas
                width: pan.contentWidth; height: pan.contentHeight
                // the grid's thumbnail, blurred, until the real picture is there
                ThumbImage {
                    id: holder
                    anchors.centerIn: parent
                    width: parent.width; height: parent.height
                    path: v.curPath; bucket: 800
                    fillMode: Image.PreserveAspectFit
                    opacity: photoImg.status === Image.Ready && !v.loading ? 0 : 1
                    visible: opacity > 0.01
                    Behavior on opacity { NumberAnimation { duration: 220 } }
                    layer.enabled: v.loading
                    layer.effect: MultiEffect { blurEnabled: true; blur: 0.42; blurMax: 14; saturation: -0.2 }
                }
                Image {
                    id: photoImg
                    anchors.fill: parent
                    source: popout.src
                    asynchronous: true; cache: false; autoTransform: true
                    fillMode: Image.PreserveAspectFit
                    smooth: true; mipmap: !v.zoomed
                    opacity: status === Image.Ready && !v.loading ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 220 } }
                }
                Image {   // focus assist: the Widgets viewer's own edge map, shown in place of the photo
                    anchors.fill: parent
                    source: popout.assistSrc
                    asynchronous: true; cache: false
                    fillMode: Image.PreserveAspectFit
                    visible: popout.assistOn && status === Image.Ready
                    smooth: true
                }
            }
        }
        HoverHandler {   // the loupe follows the pointer
            enabled: v.loupe
            onPointChanged: pan.centerOn(Math.max(0, Math.min(1, point.position.x / stage.width)), Math.max(0, Math.min(1, point.position.y / stage.height)))
        }
        // loading spinner and note
        Item {
            visible: v.loading; anchors.centerIn: parent; width: 34; height: 34
            Rectangle { anchors.fill: parent; radius: 17; color: "transparent"; border.width: 2.5; border.color: Qt.alpha(v.cFg, 0.25) }
            Shape {
                anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
                ShapePath { strokeColor: v.cFg; strokeWidth: 2.5; fillColor: "transparent"; capStyle: ShapePath.FlatCap
                    PathAngleArc { centerX: 17; centerY: 17; radiusX: 15.75; radiusY: 15.75; startAngle: -90; sweepAngle: 90 } }
                RotationAnimator on rotation { running: v.loading; from: 0; to: 360; duration: 800; loops: Animation.Infinite }
            }
            Text { anchors.horizontalCenter: parent.horizontalCenter; y: 34 + 12; text: "Loading full resolution\u2026"; color: v.cFg; font.pixelSize: 12; style: Text.Outline; styleColor: Qt.rgba(0, 0, 0, 0.6) }
        }
        Text {   // a photo that could not be read
            visible: popout.error !== "" && !v.loading; anchors.centerIn: parent; width: Math.min(parent.width - 48, 520); horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap; text: popout.error; color: v.cFg2; font.pixelSize: 13
        }
    }
    // ---- 100% zoom: where the visible part is ----
    Item {
        id: mini
        visible: v.zoomed; z: 5
        x: parent.width - 20 - width; y: parent.height - (v.fh + 100) - height
        width: 150; height: 100
        Rectangle { anchors.fill: parent; radius: 9; color: "black"; border.color: v.cLine; layer.enabled: true
            layer.effect: MultiEffect { shadowEnabled: true; shadowColor: Qt.rgba(0, 0, 0, 0.5); shadowBlur: 0.6; shadowVerticalOffset: 10 } }
        RoundClip { anchors.fill: parent; anchors.margins: 1; radius: 8
            ThumbImage { anchors.fill: parent; path: v.curPath; bucket: 240; fillMode: Image.PreserveAspectCrop; imageOpacity: 0.7 }
            Rectangle { x: pan.visibleArea.xPosition * parent.width; y: pan.visibleArea.yPosition * parent.height
                width: pan.visibleArea.widthRatio * parent.width; height: pan.visibleArea.heightRatio * parent.height
                color: "transparent"; border.color: v.cFg; border.width: 1.5; radius: 3 }
        }
    }

    // ---- the top bar ----
    Item {
        id: topBar
        width: parent.width; height: 76; z: 4
        opacity: v.zen ? 0 : 1; enabled: !v.zen
        Behavior on opacity { NumberAnimation { duration: 300 } }
        transform: Translate { y: v.zen ? -12 : 0; Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } } }
        Rectangle { anchors.fill: parent; gradient: Gradient { GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0.62) } GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0) } } }
        // back to the grid
        Item {
            id: back
            x: 18; y: 14; height: 34; width: backRow.implicitWidth + 21
            Rectangle { anchors.fill: parent; radius: 10; color: backHover.hovered ? "#2a2c31" : v.cGlass; border.color: v.cLineSoft }
            Row { id: backRow; x: 9; anchors.verticalCenter: parent.verticalCenter; spacing: 7
                Icon { anchors.verticalCenter: parent.verticalCenter; path: "M15 5l-7 7 7 7"; size: 15; color: v.cFg }
                Text { anchors.verticalCenter: parent.verticalCenter; text: "Library"; color: v.cFg; font.pixelSize: 13; font.weight: Font.Medium }
                Kbd { anchors.verticalCenter: parent.verticalCenter; text: "Esc" } }
            HoverHandler { id: backHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: v.close() }
            Accessible.role: Accessible.Button; Accessible.name: "Back to the grid"
        }
        Column {
            x: back.x + back.width + 12; y: 14; height: 34; spacing: 0; width: Math.max(60, rightRow.x - x - 12)
            Item { width: 1; height: 17
                Text { anchors.verticalCenter: parent.verticalCenter; width: parent.parent.width; elide: Text.ElideRight; text: (v.cur.name || "") + (v.info.ext ? "." + v.info.ext : ""); color: v.cFg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: 0.1 } }
            Item { width: 1; height: 17
                Text { anchors.verticalCenter: parent.verticalCenter; width: parent.parent.width; elide: Text.ElideRight; textFormat: Text.StyledText
                    text: popout.crumb + " <font color='" + v.cFg2 + "'>·</font> " + (v.info.kind || "") + (v.info.dims ? " · " + v.info.dims : ""); color: v.cFg3; font.pointSize: UiState.pt(11.5) } }
        }
        Row {
            id: rightRow
            y: 14; spacing: 12; layoutDirection: Qt.LeftToRight
            x: parent.width - 18 - width
            Item {   // flagged by Cull
                visible: (v.flag.label || "") !== ""; y: 0; width: flagRow.implicitWidth + 20; height: 28
                Rectangle { anchors.fill: parent; radius: 8; color: v.cGlass; border.color: v.cLineSoft }
                Row { id: flagRow; x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                    Rectangle { width: 7; height: 7; radius: 3.5; color: theme.rev; anchors.verticalCenter: parent.verticalCenter }
                    Text { anchors.verticalCenter: parent.verticalCenter; text: "Flagged by Cull"; color: v.cFg2; font.pointSize: UiState.pt(11.5) } }
            }
            Item {   // format and size on disk
                y: 0; width: kindRow.implicitWidth + 20; height: 28
                Rectangle { anchors.fill: parent; radius: 8; color: v.cGlass; border.color: v.cLineSoft }
                Row { id: kindRow; x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                    Text { text: v.info.ext || ""; color: v.cFg; font.pointSize: UiState.pt(11.5); font.weight: Font.Medium }
                    Text { text: v.info.size || ""; color: v.cFg2; font.pointSize: UiState.pt(11.5) } }
            }
            Item {   // 2 / 12
                width: stepRow.implicitWidth; height: 34
                Rectangle { anchors.fill: parent; radius: 10; color: v.cGlass; border.color: v.cLineSoft }
                Row { id: stepRow; height: parent.height
                    Item { width: 30; height: 34
                        Rectangle { anchors.fill: parent; anchors.margins: 1; radius: 9; color: pv.hovered ? Qt.alpha("white", 0.08) : "transparent" }
                        Icon { anchors.centerIn: parent; path: "M15 5l-7 7 7 7"; size: 15; color: pv.hovered ? v.cFg : v.cFg2 }
                        HoverHandler { id: pv; cursorShape: Qt.PointingHandCursor } TapHandler { onTapped: v.step(-1) }
                        Accessible.role: Accessible.Button; Accessible.name: "Previous photo" }
                    Item { width: Math.max(56, countText.implicitWidth + 12); height: 34
                        Text { id: countText; anchors.centerIn: parent; textFormat: Text.StyledText; color: v.cFg; font.pixelSize: 12
                            text: (v.row + 1) + " <font color='" + v.cFg3 + "'>/ " + photos.rowCount() + "</font>" } }
                    Item { width: 30; height: 34
                        Rectangle { anchors.fill: parent; anchors.margins: 1; radius: 9; color: nx.hovered ? Qt.alpha("white", 0.08) : "transparent" }
                        Icon { anchors.centerIn: parent; path: "M9 5l7 7-7 7"; size: 15; color: nx.hovered ? v.cFg : v.cFg2 }
                        HoverHandler { id: nx; cursorShape: Qt.PointingHandCursor } TapHandler { onTapped: v.step(1) }
                        Accessible.role: Accessible.Button; Accessible.name: "Next photo" }
                }
            }
            Item {   // hide the controls
                width: 34; height: 34
                Rectangle { anchors.fill: parent; radius: 10; color: zh.hovered ? "#2a2c31" : v.cGlass; border.color: v.cLineSoft }
                Icon { anchors.centerIn: parent; path: "M4 9V5h4M20 9V5h-4M4 15v4h4M20 15v4h-4"; size: 18; color: zh.hovered ? v.cFg : v.cFg2 }
                HoverHandler { id: zh; cursorShape: Qt.PointingHandCursor } TapHandler { onTapped: v.zen = true }
                Accessible.role: Accessible.Button; Accessible.name: "Hide the controls"
            }
            Item {   // close
                width: 34; height: 34
                Rectangle { anchors.fill: parent; radius: 10; color: xh.hovered ? "#2a2c31" : v.cGlass; border.color: v.cLineSoft }
                Icon { anchors.centerIn: parent; path: "M6 6l12 12M18 6L6 18"; size: 18; color: xh.hovered ? v.cFg : v.cFg2 }
                HoverHandler { id: xh; cursorShape: Qt.PointingHandCursor } TapHandler { onTapped: v.close() }
                Accessible.role: Accessible.Button; Accessible.name: "Close"
            }
        }
    }
    // a click on the picture while the controls are hidden brings them back
    TapHandler { enabled: v.zen; onTapped: v.zen = false }

    // ---- the arrows at the edges of the photo, shown while the pointer is over the viewer ----
    HoverHandler { id: over }
    Repeater {
        model: 2
        delegate: Item {
            id: edge
            required property int index
            readonly property bool isLeft: index === 0
            readonly property bool atEnd: isLeft ? v.row <= 0 : v.row >= photos.rowCount() - 1
            x: isLeft ? 18 : v.width - 18 - width; y: 76 + (v.height - (v.fh + 88) - 76) / 2 - height / 2
            width: 44; height: 44; z: 3
            opacity: over.hovered && !v.zen ? (atEnd ? 0.3 : 1) : 0
            Behavior on opacity { NumberAnimation { duration: 200 } }
            Rectangle { anchors.fill: parent; radius: 22; color: eh.hovered && !edge.atEnd ? "#2c2e33" : v.cGlass; border.color: v.cLineSoft }
            Icon { anchors.centerIn: parent; path: edge.isLeft ? "M15 5l-7 7 7 7" : "M9 5l7 7-7 7"; size: 18; color: v.cFg }
            HoverHandler { id: eh; cursorShape: edge.atEnd ? Qt.ArrowCursor : Qt.PointingHandCursor }
            TapHandler { enabled: !edge.atEnd; onTapped: v.step(edge.isLeft ? -1 : 1) }
            Accessible.role: Accessible.Button; Accessible.name: edge.isLeft ? "Previous photo" : "Next photo"
        }
    }

    // ---- Cull flagged this photo ----
    Item {
        id: banner
        visible: (v.flag.label || "") !== "" && !v.zen
        z: 7; y: 74; width: bannerRow.implicitWidth + 22; height: 44; x: (parent.width - width) / 2
        Rectangle { anchors.fill: parent; radius: 12; color: "#242019"; border.color: Qt.alpha(theme.rev, 0.55) }
        Row { id: bannerRow; x: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 12
            Icon { anchors.verticalCenter: parent.verticalCenter; path: "M12 4l9 16H3zM12 10v4M12 17h.01"; size: 15; color: theme.rev }
            Text { anchors.verticalCenter: parent.verticalCenter; text: "Cull flagged this as " + String(v.flag.label || "").toLowerCase(); color: v.cFg; font.pointSize: UiState.pt(12.5); font.weight: Font.DemiBold }
            Text { anchors.verticalCenter: parent.verticalCenter; text: "It’s in the Cull bin, not rejected yet."; color: v.cFg2; font.pointSize: UiState.pt(12.5) }
            Repeater {
                model: [{ t: "Keep it", a: "keep" }, { t: "Reject", a: "rej" }]
                delegate: Item {
                    id: bb
                    required property var modelData
                    anchors.verticalCenter: parent.verticalCenter; width: bbt.implicitWidth + 22; height: 28
                    Rectangle { anchors.fill: parent; radius: 8; color: bh.hovered ? "#31333a" : "#222428"; border.color: v.cLine }
                    Text { id: bbt; anchors.centerIn: parent; text: bb.modelData.t; color: v.cFg; font.pixelSize: 13; font.weight: Font.Medium }
                    HoverHandler { id: bh; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: { if (bb.modelData.a === "keep") popout.keepFlagged(v.row); else v.decide("rej") } }
                    Accessible.role: Accessible.Button; Accessible.name: bb.modelData.t
                }
            }
        }
    }

    // ---- the bottom: the action bar over the filmstrip ----
    Item {
        id: bottom
        width: parent.width; height: v.fh + 12 + 52; anchors.bottom: parent.bottom; z: 4
        opacity: v.zen ? 0 : 1; enabled: !v.zen
        Behavior on opacity { NumberAnimation { duration: 300 } }
        transform: Translate { y: v.zen ? 12 : 0; Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } } }
        Rectangle { anchors.fill: parent; gradient: Gradient { GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0) } GradientStop { position: 0.55; color: Qt.rgba(0, 0, 0, 0.7) } GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.7) } } }

        // the action bar
        Item {
            id: bar
            y: 0; height: 52; width: barRow.implicitWidth + 12; x: (parent.width - width) / 2
            Glass { anchors.fill: parent; frosted: false; radius: 16; tint: "#06070a"; shadowOffsetA: 14; shadowBlurA: 40; shadowAlphaA: 0.5; shadowInset: 6; shadowAlphaB: 0 }
            Rectangle { anchors.fill: parent; radius: 16; color: "transparent"; border.color: v.cLine }
            Row {
                id: barRow
                x: 6; y: 6; spacing: 6; height: 40
                Repeater {
                    model: [{ d: "win", t: "Winner", k: "W", p: "M12 20s-7-4.4-7-10a4 4 0 017-2.6A4 4 0 0119 10c0 5.6-7 10-7 10z" },
                            { d: "keep", t: "Keep", k: "K", p: "M5 12.5l4.5 4.5L19 7.5" },
                            { d: "rej", t: "Reject", k: "X", p: "M6 6l12 12M18 6L6 18" }]
                    delegate: Item {
                        id: db
                        required property var modelData
                        readonly property bool on: v.curDec === modelData.d
                        readonly property color tone: v.decColor(modelData.d)
                        width: dRow.implicitWidth + 24; height: 38; y: 1
                        scale: dp.pressed ? 0.97 : 1
                        Behavior on scale { NumberAnimation { duration: 120 } }
                        Rectangle { anchors.fill: parent; radius: 11
                            color: db.on ? Qt.tint("#0d0e11", Qt.alpha(db.tone, 0.24)) : dh.hovered ? "#222326" : v.cS2
                            border.color: db.on ? Qt.alpha(db.tone, 0.7) : v.cLineSoft }
                        Row { id: dRow; x: 11; anchors.verticalCenter: parent.verticalCenter; spacing: 7
                            Icon { anchors.verticalCenter: parent.verticalCenter; path: db.modelData.p; size: 15; color: db.tone }
                            Text { anchors.verticalCenter: parent.verticalCenter; text: db.modelData.t; color: v.cFg; font.pixelSize: 13; font.weight: Font.Medium }
                            Item { anchors.verticalCenter: parent.verticalCenter; width: dk.width + 2; height: dk.height
                                Kbd { id: dk; x: 2; text: db.modelData.k } } }
                        HoverHandler { id: dh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { id: dp; onTapped: v.decide(db.modelData.d) }
                        Accessible.role: Accessible.CheckBox; Accessible.checked: db.on; Accessible.name: db.modelData.t
                    }
                }
                Item { width: 11; height: 40; Rectangle { anchors.centerIn: parent; width: 1; height: 26; color: v.cLine } }
                Row {   // the rating
                    height: 40; leftPadding: 4; rightPadding: 4; spacing: 1
                    Repeater {
                        model: 5
                        delegate: Item {
                            id: st
                            required property int index
                            width: 26; height: 34; y: 3
                            readonly property bool on: v.curRating >= index + 1
                            Icon { anchors.centerIn: parent; path: "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8 6.8 19.6l1-5.8-4.3-4.1 5.9-.8z"; size: 17
                                color: st.on || sh.hovered ? theme.star : v.cFg3; filled: st.on }
                            HoverHandler { id: sh; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: popout.rate(v.row, st.index + 1) }
                            Accessible.role: Accessible.Button; Accessible.name: (st.index + 1) + (st.index === 0 ? " star" : " stars")
                        }
                    }
                }
                Item { width: 11; height: 40; Rectangle { anchors.centerIn: parent; width: 1; height: 26; color: v.cLine } }
                Repeater {
                    model: [{ id: "focus", k: "F", tip: "Focus assist (F, Shift+F colour)", icon: "focusAssist" },
                            { id: "loupe", k: "L", tip: "Loupe (L)", icon: "loupe" },
                            { id: "move", k: "M", tip: "Move to… (M)", icon: "movePhoto" },
                            { id: "tag", k: "T", tip: "Tag (T)", icon: "photoTag" },
                            { id: "coll", k: "B", tip: "Add to a collection (B)", icon: "addCollection" },
                            { id: "del", k: "Del", tip: "Move to the Recycle Bin (Delete)", icon: "trash" }]
                    delegate: Item {
                        id: tb
                        required property var modelData
                        readonly property bool on: modelData.id === "focus" ? popout.assistOn : modelData.id === "loupe" ? v.loupe : modelData.id === "coll" ? v.collOpen : false
                        width: 38; height: 38; y: 1
                        Rectangle { anchors.fill: parent; radius: 11; color: tb.on ? Qt.alpha(theme.accent, 0.2) : th.hovered ? "#202124" : "transparent" }
                        Icon { anchors.horizontalCenter: parent.horizontalCenter; y: 5; name: tb.modelData.icon; size: 20; strokeWidth: 1.7; color: tb.on ? theme.accentFg : th.hovered ? v.cFg : v.cFg2 }
                        Text { x: parent.width - width - 2; y: parent.height - height - 1; text: tb.modelData.k; color: v.cFg3; opacity: 0.9; font.pointSize: UiState.pt(8.5); font.weight: Font.Medium }
                        HoverHandler { id: th; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: {
                                var id = tb.modelData.id
                                if (id === "focus") popout.toggleAssist()
                                else if (id === "loupe") v.toggleLoupe()
                                else if (id === "move") v.moveRequested(v.row)
                                else if (id === "tag") popout.notice("Tagging isn’t connected to this window yet")
                                else if (id === "coll") v.collOpen = !v.collOpen
                                else if (id === "del") popout.delete(v.row)
                            }
                        }
                        HoverTip { tip: tb.modelData.tip; hovered: th.hovered }
                        Component.onCompleted: { if (modelData.id === "coll") v.collButton = tb }
                        Accessible.role: Accessible.Button; Accessible.name: modelData.tip
                    }
                }
                Item { width: 11; height: 40; Rectangle { anchors.centerIn: parent; width: 1; height: 26; color: v.cLine } }
                Item {   // Edit in PhotoCraft: the one accent button
                    id: edit
                    readonly property bool can: popout.canEdit && UiState.vwScene !== "noedit"
                    width: eRow.implicitWidth + 26; height: 38; y: 1
                    scale: ep.pressed && can ? 0.98 : 1
                    Rectangle { anchors.fill: parent; radius: 11
                        color: edit.can ? (eh2.hovered ? Qt.lighter(theme.accent, 1.08) : theme.accent) : "#1c1d20"
                        border.color: edit.can ? Qt.tint(theme.accent, Qt.rgba(1, 1, 1, 0.25)) : v.cLineSoft }
                    Row { id: eRow; x: 12; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                        Icon { anchors.verticalCenter: parent.verticalCenter; path: "M4 20l1-4.2L16.6 4.2a2 2 0 012.8 0l.4.4a2 2 0 010 2.8L8.2 19zM14.5 6.4l3.1 3.1"; size: 18; color: edit.can ? "#080e16" : v.cFg3 }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: edit.can ? "Edit in PhotoCraft" : "Edit"; color: edit.can ? "#080e16" : v.cFg3; font.pixelSize: 13; font.weight: Font.DemiBold }
                        Item { id: ek; anchors.verticalCenter: parent.verticalCenter; width: ekt.implicitWidth + 12; height: 18
                            Rectangle { anchors.fill: parent; radius: 5; color: edit.can ? Qt.rgba(1, 1, 1, 0.28) : "transparent"; border.color: edit.can ? Qt.alpha("#080e16", 0.35) : v.cLine }
                            Text { id: ekt; anchors.centerIn: parent; text: "E"; color: edit.can ? "#080e16" : v.cFg3; font.pixelSize: 10; font.weight: Font.Medium } } }
                    HoverHandler { id: eh2; cursorShape: edit.can ? Qt.PointingHandCursor : Qt.ArrowCursor }
                    TapHandler { id: ep; onTapped: v.editor() }
                    HoverTip { tip: edit.can ? "Open this photo in PhotoCraft (E)" : "PhotoCraft is not installed. Install it from Settings › Editor."; hovered: eh2.hovered }
                    Accessible.role: Accessible.Button; Accessible.name: edit.can ? "Edit in PhotoCraft" : "Edit in PhotoCraft (not connected)"
                }
            }
        }

        // the filmstrip: full width, the photo you are on in the middle, the two beside it lifted a little
        Item {
            id: strip
            y: 52 + 12; width: parent.width; height: v.fh
            Rectangle { anchors.fill: parent; color: Qt.rgba(6 / 255, 7 / 255, 10 / 255, 0.88) }
            Rectangle { width: parent.width; height: 1; color: v.cLineSoft }
            readonly property int th: Math.round((v.fh - 16) / 1.25)
            readonly property int tw: Math.round(th * 1.5)
            readonly property int lift: Math.round(th * 0.1)
            ListView {
                id: film
                anchors.fill: parent; anchors.topMargin: 8
                orientation: ListView.Horizontal
                model: photos
                spacing: 7; clip: false
                interactive: false
                cacheBuffer: 2000
                leftMargin: width / 2 - strip.tw / 2; rightMargin: width / 2 - strip.tw / 2
                currentIndex: Math.max(0, v.row)
                highlightRangeMode: ListView.StrictlyEnforceRange
                preferredHighlightBegin: width / 2 - strip.tw / 2; preferredHighlightEnd: width / 2 + strip.tw / 2
                highlightMoveDuration: grip.dragging || !v.shown ? 0 : 550
                highlight: Item {}
                highlightFollowsCurrentItem: true
                delegate: Item {
                    id: fs
                    required property int index
                    required property string path
                    required property string decision
                    required property string name
                    readonly property int dist: Math.abs(index - v.row)
                    readonly property real target: dist === 0 ? 1 : dist === 1 ? 0.4 : 0
                    property real l: target
                    Behavior on l { enabled: !grip.dragging && v.shown; SequentialAnimation { PauseAnimation { duration: fs.dist * 18 } NumberAnimation { duration: 550; easing.type: Easing.OutQuint } } }
                    readonly property bool cur: dist === 0
                    readonly property bool flagged: { void cull.view; return (popout.flagFor(index).label || "") !== "" }
                    width: strip.tw; height: strip.th
                    y: (film.height - strip.th) / 2 + 4 - 8 - l * strip.lift
                    opacity: 0.5 + l * 0.5
                    Rectangle { anchors.fill: parent; radius: 7; color: "#111214"; border.width: 2; border.color: fs.cur ? v.cFg : "transparent"
                        layer.enabled: fs.cur; layer.effect: MultiEffect { shadowEnabled: true; shadowColor: Qt.rgba(0, 0, 0, 0.55); shadowBlur: 0.5; shadowVerticalOffset: 10 } }
                    RoundClip { anchors.fill: parent; anchors.margins: 2; radius: 5
                        ThumbImage { anchors.fill: parent; path: fs.path; bucket: strip.th > 100 ? 360 : 240; desaturate: fs.decision === "rej" ? 0.9 : 0 }
                        Rectangle { anchors.fill: parent; color: "black"; opacity: fs.decision === "rej" ? 0.4 : 0 }
                        Rectangle { visible: fs.flagged; anchors.bottom: parent.bottom; width: parent.width; height: 3; color: v.decColor("rev") }
                    }
                    Rectangle { x: parent.width - 4 - 9 - 2; y: 4 + 2; width: 9; height: 9; radius: 4.5; color: v.decColor(fs.decision); border.width: 1.5; border.color: Qt.rgba(0, 0, 0, 0.55) }
                    Text { x: 6; y: parent.height - height - 5; text: fs.index + 1; visible: fs.cur || fh.hovered; color: "white"; font.pixelSize: 9; font.weight: Font.DemiBold; style: Text.Outline; styleColor: Qt.rgba(0, 0, 0, 0.6) }
                    HoverHandler { id: fh; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: v.go(fs.index) }
                    Accessible.role: Accessible.Button; Accessible.name: fs.name
                }
            }
            // the grip on the strip's top edge: drag it up for a taller strip and bigger thumbnails, down for a shorter one with more of them
            Item {
                id: grip
                property bool dragging: false
                x: 0; y: -5; width: parent.width; height: 14; z: 30
                Rectangle { anchors.horizontalCenter: parent.horizontalCenter; y: 5; width: gh.hovered || grip.dragging ? 60 : 44; height: 4; radius: 2; color: gh.hovered || grip.dragging ? v.cFg3 : v.cLine
                    Behavior on width { NumberAnimation { duration: 150 } } }
                HoverHandler { id: gh; cursorShape: Qt.SizeVerCursor }
                MouseArea {
                    id: gm
                    anchors.fill: parent; cursorShape: Qt.SizeVerCursor
                    property real y0: 0; property int h0: 0
                    onPressed: (m) => { y0 = gm.mapToItem(v, m.x, m.y).y; h0 = v.fh; grip.dragging = true; grip.forceActiveFocus() }
                    onPositionChanged: (m) => { if (pressed) popout.setStripHeight(h0 + (y0 - gm.mapToItem(v, m.x, m.y).y)) }
                    onReleased: { grip.dragging = false; popout.saveStripHeight() }
                }
                activeFocusOnTab: true
                Keys.onUpPressed: { popout.setStripHeight(v.fh + 16); popout.saveStripHeight() }
                Keys.onDownPressed: { popout.setStripHeight(v.fh - 16); popout.saveStripHeight() }
                Keys.onPressed: (e) => { if (e.key !== Qt.Key_Up && e.key !== Qt.Key_Down) { v.forceActiveFocus(); e.accepted = false } }
                Accessible.role: Accessible.Slider; Accessible.name: "Resize the filmstrip. Drag, or use the up and down arrow keys."
            }
        }
    }

    // ---- zoom pill and key hints, above the strip ----
    Item {
        id: zoomPill
        x: 20; y: parent.height - (v.fh + 16) - height; width: zr.implicitWidth; height: 30; z: 5
        opacity: v.zen ? 0 : 1; Behavior on opacity { NumberAnimation { duration: 300 } }
        Rectangle { anchors.fill: parent; radius: 9; color: v.cGlass; border.color: v.cLineSoft }
        Row { id: zr; height: parent.height
            Repeater {
                model: [{ t: "Fit", k: "0", z: false }, { t: "100%", k: "Z", z: true }]
                delegate: Item {
                    id: zb
                    required property var modelData
                    readonly property bool on: v.zoomed === modelData.z
                    width: zbr.implicitWidth + 22; height: 30
                    Rectangle { anchors.fill: parent; anchors.margins: 1; radius: 8; color: zb.on ? Qt.alpha(theme.accent, 0.22) : "transparent" }
                    Row { id: zbr; x: 11; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                        Text { anchors.verticalCenter: parent.verticalCenter; text: zb.modelData.t; color: zb.on ? theme.accentFg : zz.hovered ? v.cFg : v.cFg2; font.pixelSize: 12 }
                        Kbd { anchors.verticalCenter: parent.verticalCenter; text: zb.modelData.k } }
                    HoverHandler { id: zz; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: { v.zoomed = zb.modelData.z; if (!v.zoomed) v.loupe = false } }
                    Accessible.role: Accessible.RadioButton; Accessible.checked: zb.on; Accessible.name: zb.modelData.t === "Fit" ? "Fit the photo" : "Show at 100%"
                }
            }
        }
    }
    Row {
        x: parent.width - 20 - width; y: parent.height - (v.fh + 26) - height; z: 5; spacing: 10
        opacity: v.zen ? 0 : 1; Behavior on opacity { NumberAnimation { duration: 300 } }
        Row { spacing: 4; Kbd { text: "←" } Kbd { text: "→" } Text { text: "step"; color: v.cFg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter; leftPadding: 2 } }
        Row { spacing: 4; Kbd { text: "Space" } Text { text: "close"; color: v.cFg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter; leftPadding: 2 } }
    }

    // ---- what just happened ----
    Item {
        id: toast
        readonly property var t: popout.toast
        visible: !!t.text; z: 8
        width: trow.implicitWidth + 21; height: 38
        x: (parent.width - width) / 2; y: parent.height - (v.fh + 84) - height
        readonly property color tone: t.kind === "win" ? theme.win : t.kind === "keep" ? theme.keep : t.kind === "rej" ? theme.rej : "#9aa0aa"
        onTChanged: { if (t.text) { opacity = 0; scale = 0.97; enter.restart() } }
        ParallelAnimation { id: enter
            NumberAnimation { target: toast; property: "opacity"; to: 1; duration: 220 }
            NumberAnimation { target: toast; property: "scale"; to: 1; duration: 300; easing.type: Easing.OutCubic } }
        Rectangle { anchors.fill: parent; radius: 12; color: "#191b1e"; border.color: v.cLine
            layer.enabled: true; layer.effect: MultiEffect { shadowEnabled: true; shadowColor: Qt.rgba(0, 0, 0, 0.55); shadowBlur: 0.7; shadowVerticalOffset: 12 } }
        Row { id: trow; x: 13; anchors.verticalCenter: parent.verticalCenter; spacing: 10
            Icon { anchors.verticalCenter: parent.verticalCenter; size: 16; color: toast.tone
                path: toast.t.kind === "win" ? "M12 20s-7-4.4-7-10a4 4 0 017-2.6A4 4 0 0119 10c0 5.6-7 10-7 10z" : toast.t.kind === "keep" ? "M5 12.5l4.5 4.5L19 7.5" : toast.t.kind === "rej" ? "M6 6l12 12M18 6L6 18" : "M12 8h.01M11 12h1v4h1" }
            Text { anchors.verticalCenter: parent.verticalCenter; text: toast.t.text || ""; color: v.cFg; font.pointSize: UiState.pt(12.5) }
            Item { visible: !!toast.t.undo; anchors.verticalCenter: parent.verticalCenter; width: ut.implicitWidth + 18; height: 26
                Rectangle { anchors.fill: parent; radius: 7; color: uh.hovered ? "#2a2c31" : "transparent" }
                Text { id: ut; anchors.centerIn: parent; text: "Undo"; color: theme.accentFg; font.pixelSize: 13; font.weight: Font.Medium }
                HoverHandler { id: uh; cursorShape: Qt.PointingHandCursor } TapHandler { onTapped: { popout.undo() } }
                Accessible.role: Accessible.Button; Accessible.name: "Undo" } }
    }

    // ---- add to a collection ----
    property Item collButton: null
    Item {
        id: collScrim
        visible: v.collOpen; anchors.fill: parent; z: 8
        TapHandler { onTapped: v.collOpen = false }
    }
    Item {
        id: collPop
        visible: v.collOpen; z: 9; width: 248; height: collCol.implicitHeight + 12
        readonly property var rows: { if (!collections || !photos) return []; void collections.revision; void photos.revision; return v.collOpen ? popout.collectionRows(v.row) : [] }
        x: { void bar.x; void bar.width; void v.width; var b = v.collButton ? v.collButton.mapToItem(v, v.collButton.width / 2, 0).x : v.width / 2 + 130; return Math.max(12, Math.min(v.width - width - 12, b - width / 2)) }
        y: parent.height - (v.fh + 52) - height
        Rectangle { anchors.fill: parent; radius: 14; color: "#141619"; border.color: v.cLine
            layer.enabled: true; layer.effect: MultiEffect { shadowEnabled: true; shadowColor: Qt.rgba(0, 0, 0, 0.6); shadowBlur: 0.9; shadowVerticalOffset: 18 } }
        Column { id: collCol; x: 6; y: 6; width: parent.width - 12
            Text { x: 10; topPadding: 6; bottomPadding: 6; text: ("ADD " + (v.cur.name || "") + " TO…").toUpperCase(); color: v.cFg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.9 }
            Repeater {
                model: collPop.rows
                delegate: Item {
                    id: cr
                    required property var modelData
                    width: collCol.width; height: 34
                    Rectangle { anchors.fill: parent; radius: 9; color: crh.hovered ? "#232529" : "transparent" }
                    Rectangle { x: 10; anchors.verticalCenter: parent.verticalCenter; width: 12; height: 12; radius: 4; color: cr.modelData.color }
                    Text { x: 32; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 32 - 34; elide: Text.ElideRight; text: cr.modelData.name; color: v.cFg; font.pixelSize: 13 }
                    Icon { visible: cr.modelData.on; x: parent.width - 10 - width; anchors.verticalCenter: parent.verticalCenter; path: "M5 12.5l4.5 4.5L19 7.5"; size: 15; color: theme.accentFg }
                    HoverHandler { id: crh; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: popout.toggleCollection(v.row, cr.modelData.id) }
                    Accessible.role: Accessible.CheckBox; Accessible.checked: cr.modelData.on; Accessible.name: cr.modelData.name
                }
            }
            Rectangle { x: 4; width: parent.width - 8; height: 1; color: "#1e1f22" }
            Item { width: collCol.width; height: 34
                Rectangle { anchors.fill: parent; radius: 9; color: nch.hovered ? "#232529" : "transparent" }
                Icon { x: 10; anchors.verticalCenter: parent.verticalCenter; path: "M12 5v14M5 12h14"; size: 15; color: theme.accentFg }
                Text { x: 35; anchors.verticalCenter: parent.verticalCenter; text: "New collection…"; color: theme.accentFg; font.pixelSize: 13 }
                HoverHandler { id: nch; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: { v.collOpen = false; UiState.collName = ""; UiState.collDlgId = ""; UiState.collDlg = "new" } }
                Accessible.role: Accessible.Button; Accessible.name: "New collection" }
        }
    }

    // ---- opening the editor ----
    Rectangle {
        id: openScreen
        visible: v.opening; anchors.fill: parent; z: 20; color: Qt.rgba(3 / 255, 4 / 255, 5 / 255, 0.9)
        MouseArea { anchors.fill: parent }
        Column { anchors.centerIn: parent; spacing: 18; width: 420
            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; width: 62; height: 62; radius: 17
                gradient: Gradient { GradientStop { position: 0; color: Qt.lighter(theme.accent, 1.12) } GradientStop { position: 1; color: Qt.darker(theme.accent, 1.9) } }
                Icon { anchors.centerIn: parent; size: 32; strokeWidth: 1.7; color: "white"; path: "M12 3.5a8.5 8.5 0 100 17 8.5 8.5 0 000-17zM12 3.5l3 8.5M20.5 12l-8.5 3M12 20.5L9 12M3.5 12l8.5-3" } }
            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "Opening " + (v.cur.name || "") + (v.info.ext ? "." + v.info.ext : "") + " in PhotoCraft…"; color: v.cFg; font.pixelSize: 17; font.weight: Font.DemiBold }
            Text { width: parent.width; y: -10; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; text: "Your decisions here are saved. Closing the editor brings you back to this photo."; color: v.cFg3; font.pointSize: UiState.pt(12.5) }
            Item { anchors.horizontalCenter: parent.horizontalCenter; width: 220; height: 4; clip: true
                Rectangle { anchors.fill: parent; radius: 2; color: "#242528" }
                Rectangle { id: bar2; width: 88; height: 4; radius: 2; color: theme.accent; x: -88
                    SequentialAnimation on x { running: v.opening; loops: Animation.Infinite; NumberAnimation { from: -88; to: 220; duration: 1200; easing.type: Easing.InOutQuad } } } }
            Item { anchors.horizontalCenter: parent.horizontalCenter; width: cr2.implicitWidth + 24; height: 30
                Rectangle { anchors.fill: parent; radius: 8; color: "transparent"; border.color: v.cLine }
                Row { id: cr2; x: 12; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                    Text { text: "Cancel"; color: v.cFg2; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
                    Kbd { text: "Esc"; anchors.verticalCenter: parent.verticalCenter } }
                TapHandler { onTapped: v.opening = false } }
        }
    }

    component HoverTip: Item {
        id: ht
        property string tip
        property bool hovered: false
        property bool ready: false
        anchors.fill: parent
        onHoveredChanged: { if (!hovered) ready = false }
        Timer { interval: 500; running: ht.hovered; onTriggered: ht.ready = true }
        Tip { text: ht.tip; above: true; show: ht.ready && ht.hovered }
    }
}
