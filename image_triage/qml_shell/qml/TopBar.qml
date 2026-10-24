import QtQuick
import QtQuick.Shapes
import QtQuick.Window

// The slim application bar: brand icon, menus, the command search, theme and inspector toggles, window controls.
// The window has no system frame, so this bar is its title bar: dragging an empty part moves the window (the system's own move, so
// Windows snapping works), a double click maximises or restores it, and the three buttons at the right are real.
Rectangle {
    id: top
    height: 42
    color: theme.s1
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }

    readonly property var win: Window.window
    readonly property bool maximized: win !== null && win.visibility === Window.Maximized
    function onTitle(p) { var c = top.childAt(p.x, p.y); return c === null || c === brand }       // not over a menu, the search field or a button
    function windowAction(what) {
        if (what === "winMin") win.showMinimized()
        else if (what === "winMax") { if (maximized) win.showNormal(); else win.showMaximized() }
        else win.close()
    }
    DragHandler {
        target: null; enabled: top.win !== null && top.win.visibility === Window.Windowed
        onActiveChanged: if (active && top.onTitle(centroid.pressPosition)) top.win.startSystemMove()
    }
    TapHandler { acceptedButtons: Qt.LeftButton; onDoubleTapped: (point) => { if (top.onTitle(point.position)) top.windowAction("winMax") } }

    readonly property real brandW: UiState.topAligned ? 72 : 30
    property real helpX: 205
    function setHelpX(v) { helpX = v }                  // where the Help menu opens: under the Help button

    Item {
        id: brand
        width: top.brandW; height: parent.height
        Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
        Shape {
            x: UiState.topAligned ? (parent.width - 18) / 2 : 12; anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18; preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: ConicalGradient {
                    centerX: 9; centerY: 9; angle: 210
                    GradientStop { position: 0.0; color: theme.accent }
                    GradientStop { position: 0.5; color: "#c58ae6" }
                    GradientStop { position: 1.0; color: theme.accent }
                }
                PathSvg { path: Icons.rect(0, 0, 18, 18, 6) }
            }
        }
        Accessible.name: "Image Triage"
        Accessible.role: Accessible.Graphic
    }
    Row {
        id: menus
        x: UiState.topAligned ? brand.width + 14 : brand.width + 12
        Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
        anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -0.6
        visible: top.width > 900
        spacing: 2
        Repeater {
            model: ["File", "Review", "View", "AI", "Help"]
            delegate: Item {
                required property string modelData
                width: lbl.implicitWidth + 18; height: 28
                Rectangle { anchors.fill: parent; radius: 7; color: Qt.alpha(theme.lift, h.hovered ? 0.08 : 0) }
                Text { id: lbl; anchors.centerIn: parent; text: modelData; color: h.hovered ? theme.fg : theme.fg2; font.pixelSize: 13 }
                HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
                readonly property real gx: menus.x + x
                onGxChanged: if (modelData === "Help") setHelpX(gx)
                Component.onCompleted: if (modelData === "Help") setHelpX(gx)
                TapHandler { enabled: modelData === "Help"; onTapped: reference.toggleMenu() }
                Accessible.role: Accessible.MenuItem; Accessible.name: modelData
            }
        }
    }
    Rectangle {
        id: cmd
        // flex: 1, max 460, margin: 0 auto: the free space is shared by the two auto margins of the field and the one of the right group
        readonly property real left0: (menus.visible ? menus.x + menus.width : brand.width) + 6
        readonly property real avail: rightRow.x - 6 - left0
        width: Math.max(0, Math.min(460, avail)); height: 28; radius: 9
        x: UiState.compact ? left0 : left0 + Math.max(0, avail - 460) / 3      // compact: margin 0
        anchors.verticalCenter: parent.verticalCenter
        color: ch.hovered ? theme.s2 : theme.base; border.color: ch.hovered ? theme.line : theme.lineSoft
        Behavior on color { ColorAnimation { duration: 150 } }
        Row {
            anchors.verticalCenter: parent.verticalCenter; x: 10; spacing: 8
            Icon { name: "search"; size: 18; color: theme.fg; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Search photos, or run a command"; color: theme.fg; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 7; anchors.verticalCenter: parent.verticalCenter
            text: "Ctrl K"; color: theme.fg3; font.pixelSize: 10; font.weight: Font.Medium; leftPadding: 6; rightPadding: 6; topPadding: 1.5; bottomPadding: 1.5
            Rectangle { anchors.fill: parent; z: -1; radius: 5; color: "transparent"; border.color: theme.line }
            Rectangle { x: 1; y: parent.height - 2; width: parent.width - 2; height: 1.5; radius: 1; color: theme.line }
        }
        HoverHandler { id: ch; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: UiState.openPalette("main") }
        Accessible.role: Accessible.Button; Accessible.name: "Search photos or run a command"
    }
    Row {
        id: rightRow
        anchors.right: winButtons.left; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; spacing: 2
        Repeater {
            model: [{ i: "sun", tip: "Switch light or dark theme" }, { i: "inspector", tip: "Show or hide the inspector" }]
            delegate: Item {
                required property var modelData
                width: 32; height: 32; y: 4.5
                readonly property bool on: modelData.i === "inspector" && UiState.insp
                Rectangle { anchors.fill: parent; radius: 9; color: on ? Qt.alpha(theme.accent, 0.16) : Qt.alpha(theme.lift, bh.hovered ? 0.08 : 0) }
                Icon { anchors.centerIn: parent; name: modelData.i; size: 18; color: on ? theme.accentFg : theme.fg }   // `.app button{color:inherit}` outranks `.ibtn`, so the idle icon is the full text colour
                HoverHandler { id: bh; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: { if (modelData.i === "sun") { settings.set("theme", theme.light ? "slate" : "light"); settings.save() } else UiState.insp = !UiState.insp } }
                Accessible.role: Accessible.Button; Accessible.name: modelData.tip
            }
        }
    }
    Row {   // minimise, maximise or restore, close: flush with the window's corner, like a system title bar
        id: winButtons
        anchors.right: parent.right; anchors.top: parent.top
        Repeater {
            model: ["winMin", "winMax", "winClose"]
            delegate: Item {
                id: wb
                required property string modelData
                readonly property bool isClose: modelData === "winClose"
                width: 38; height: 41
                Rectangle { anchors.fill: parent; color: wb.isClose ? theme.rej : Qt.alpha(theme.lift, 0.08); visible: wh.hovered }
                Icon { anchors.centerIn: parent; name: wb.modelData === "winMax" && top.maximized ? "winRestore" : wb.modelData; size: 12; color: wb.isClose && wh.hovered ? "white" : theme.fg3 }
                HoverHandler { id: wh }
                TapHandler { onTapped: top.windowAction(wb.modelData) }
                Accessible.role: Accessible.Button
                Accessible.name: wb.isClose ? "Close" : wb.modelData === "winMin" ? "Minimize" : top.maximized ? "Restore" : "Maximize"
            }
        }
    }
}
