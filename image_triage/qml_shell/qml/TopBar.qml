import QtQuick
import QtQuick.Shapes
import QtQuick.Window
import QtQuick.Controls

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
    function focusSearch() { searchInput.forceActiveFocus(); searchInput.selectAll() }

    Item {
        id: brand
        width: top.brandW; height: parent.height
        Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
        LogoMark { x: UiState.topAligned ? (parent.width - 28) / 2 : 6; anchors.verticalCenter: parent.verticalCenter; size: 28 }
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
                id: title
                required property string modelData
                objectName: "menuTitle-" + modelData
                width: lbl.implicitWidth + 18; height: 28
                readonly property string key: modelData.toLowerCase()
                readonly property bool shown: modelData === "Help" ? reference.menuOpen : UiState.topMenu === key     // this one's menu is open
                // File, Review, View and AI open their menu (TopMenus.qml), Help its own (HelpMenu.qml); one at a time
                function show() {
                    if (modelData === "Help") { UiState.topMenu = ""; UiState.topSub = -1; if (!reference.menuOpen) reference.toggleMenu() }
                    else { if (reference.menuOpen) reference.closeMenu(); UiState.topSub = -1; UiState.topMenuX = gx; UiState.topMenu = key }
                }
                Rectangle { anchors.fill: parent; radius: 7; color: Qt.alpha(theme.lift, h.hovered || title.shown ? 0.08 : 0) }
                Text { id: lbl; anchors.centerIn: parent; text: modelData; color: h.hovered || title.shown ? theme.fg : theme.fg2; font.pixelSize: 13 }
                HoverHandler {
                    id: h; cursorShape: Qt.PointingHandCursor
                    // with one menu open, moving over another title opens that one, as in any menu bar
                    onHoveredChanged: if (hovered && !title.shown && (UiState.topMenu !== "" || reference.menuOpen)) title.show()
                }
                readonly property real gx: menus.x + x
                onGxChanged: if (modelData === "Help") setHelpX(gx)
                Component.onCompleted: if (modelData === "Help") setHelpX(gx)
                TapHandler { onTapped: { if (title.shown) { UiState.topMenu = ""; UiState.topSub = -1; if (reference.menuOpen) reference.closeMenu() } else title.show() } }
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
        x: UiState.compact ? left0 : Math.max(left0, Math.min((parent.width - width) / 2, rightRow.x - 6 - width))      // centred on the window while there is room (the mockup's auto margins left it off-centre on wide screens); compact: margin 0
        anchors.verticalCenter: parent.verticalCenter
        color: ch.hovered ? theme.s2 : theme.base; border.color: searchInput.activeFocus ? theme.accent : ch.hovered ? theme.line : theme.lineSoft
        Behavior on color { ColorAnimation { duration: 150 } }
        Icon { x: 10; name: "search"; size: 16; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
        TextInput {
            id: searchInput; objectName: "photoSearchInput"
            x: 34; width: Math.max(0, commandButton.x - x - (clearSearch.visible ? 27 : 8)); anchors.verticalCenter: parent.verticalCenter
            color: theme.fg; font.pixelSize: 13; clip: true; selectByMouse: true
            text: photos.searchText
            onTextEdited: photos.setSearch(text)
            Keys.onEscapePressed: { photos.setSearch(""); focus = false }
            Text { visible: !parent.text; width: parent.width; elide: Text.ElideRight; text: "Search photos…"; color: theme.fg3; font: parent.font }
            Accessible.name: "Search photos"; Accessible.description: photos.searchStatus
        }
        Item { id: clearSearch; objectName: "clearPhotoSearch"; visible: !!photos.searchText; width: 24; height: 26; x: commandButton.x - width - 4
            Icon { anchors.centerIn: parent; name: "x"; size: 13; color: theme.fg3; path: "M6 6l12 12M6 18L18 6" }
            TapHandler { onTapped: { photos.setSearch(""); searchInput.forceActiveFocus() } }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            Accessible.role: Accessible.Button; Accessible.name: "Clear photo search"
        }
        Text {
            id: commandButton; objectName: "searchCommandsButton"
            anchors.right: parent.right; anchors.rightMargin: 7; anchors.verticalCenter: parent.verticalCenter
            text: cmd.width > 300 ? "Commands · Ctrl K" : "Ctrl K"; color: theme.fg3; font.pixelSize: 10; font.weight: Font.Medium; leftPadding: 6; rightPadding: 6; topPadding: 1.5; bottomPadding: 1.5
            Rectangle { anchors.fill: parent; z: -1; radius: 5; color: "transparent"; border.color: theme.line }
            Rectangle { x: 1; y: parent.height - 2; width: parent.width - 2; height: 1.5; radius: 1; color: theme.line }
            TapHandler { onTapped: UiState.openPalette("main") }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            Accessible.role: Accessible.Button; Accessible.name: "Open command palette"
        }
        HoverHandler { id: ch }
        ToolTip { id: searchTip; visible: searchInput.activeFocus && !!photos.searchText && !!photos.searchStatus
            text: photos.searchStatus; width: Math.min(480, implicitWidth)
            contentItem: Text { text: searchTip.text; color: theme.fg2; font.pixelSize: 12; wrapMode: Text.WordWrap }
            background: Rectangle { radius: 8; color: theme.s2; border.color: theme.lineSoft }
        }
    }
    Row {
        id: rightRow
        anchors.right: winButtons.left; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -0.5; spacing: 2   // centred in the 41 px above the 1 px line
        Repeater {
            model: [{ i: "sun", tip: "Switch light or dark theme" }, { i: "inspector", tip: "Show or hide the inspector" }]
            delegate: Item {
                required property var modelData
                width: 32; height: 32          // the Row centres it: a y of its own here pushed the highlight down onto the line
                readonly property bool on: modelData.i === "inspector" && UiState.insp
                Rectangle { anchors.fill: parent; radius: 9; color: on ? Qt.alpha(theme.accent, 0.16) : Qt.alpha(theme.lift, bh.hovered ? 0.08 : 0) }
                Icon { anchors.centerIn: parent; name: modelData.i; size: 18; color: on ? theme.accentFg : theme.fg }   // `.app button{color:inherit}` outranks `.ibtn`, so the idle icon is the full text colour
                HoverHandler { id: bh; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: { if (modelData.i === "sun") { settings.toggleLight() } else UiState.insp = !UiState.insp } }
                Accessible.role: Accessible.Button; Accessible.name: modelData.tip
            }
        }
    }
    Row {   // minimise, maximise or restore, close: flush with the window's corner, like a system title bar
        id: winButtons
        anchors.right: parent.right; anchors.top: parent.top
        // Inside the delegates below `top` is the delegate's own anchor line, not this bar (a delegate's context looks at its item first), so they
        // reach the bar through these.
        readonly property bool maximized: top.maximized
        function act(what) { top.windowAction(what) }
        Repeater {
            model: ["winMin", "winMax", "winClose"]
            delegate: Item {
                id: wb
                required property string modelData
                readonly property bool isClose: modelData === "winClose"
                width: 46; height: 41
                Rectangle { anchors.fill: parent; color: wb.isClose ? theme.rej : Qt.alpha(theme.lift, 0.08); visible: wh.hovered }
                Icon { anchors.centerIn: parent; name: wb.modelData === "winMax" && winButtons.maximized ? "winRestore" : wb.modelData; size: 18; strokeWidth: 1.8; color: wb.isClose && wh.hovered ? "white" : theme.fg2 }
                HoverHandler { id: wh }
                TapHandler { onTapped: winButtons.act(wb.modelData) }
                Accessible.role: Accessible.Button
                Accessible.name: wb.isClose ? "Close" : wb.modelData === "winMin" ? "Minimize" : winButtons.maximized ? "Restore" : "Maximize"
            }
        }
    }
}
