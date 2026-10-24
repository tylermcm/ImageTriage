import QtQuick
import QtQuick.Window

// The photo menu and the folder menu, with their fly-out submenu. The model is menu_model.py (`ctxMenu`); this file draws its `view` where the
// pointer was. Rows, sizes and colours follow the mockup (scripts/qml_parity/menu.dc.html).
Item {
    id: root
    objectName: "contextMenu"
    readonly property var v: ctxMenu.view
    property real px: 0
    property real py: 0
    anchors.fill: parent
    visible: v.open
    z: 125
    readonly property var paths: ({
        win: "M12 4l2.4 5 5.4.6-4 3.7 1.1 5.4L12 16l-4.9 2.7 1.1-5.4-4-3.7 5.4-.6z", keep: "M4.5 12.5l4.5 4.5 10.5-10.5", rej: "M6 6l12 12M18 6L6 18",
        open: "M4 6h6l2 2.5h8V18H4z", editor: "M4 20l4-1 11-11-3-3L5 16zM14 7l3 3", "with": "M14 4h6v6M20 4l-9 9M18 14v5H5V6h5", phone: "M8 3h8a1 1 0 011 1v16a1 1 0 01-1 1H8a1 1 0 01-1-1V4a1 1 0 011-1zM11 18h2",
        coll: "M12 4l9 5-9 5-9-5zM3 14l9 5 9-5", move: "M8 8h11v11H8zM5 16V5h11", rename: "M4 20h4L19 9l-4-4L4 16z", resize: "M4 14v6h6M20 10V4h-6M4 20L20 4", convert: "M7 7h11l-3-3M17 17H6l3 3",
        link: "M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1", text: "M5 7h14M5 12h14M5 17h9", folder: "M3.5 7.5a2 2 0 012-2h4l2 2.5h7a2 2 0 012 2v7.5a2 2 0 01-2 2h-13a2 2 0 01-2-2z",
        trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13", refresh: "M20 11a8 8 0 10-2.3 5.7M20 4v7h-7", view: "M4 5h7v7H4zM13 5h7v7h-7zM4 14h7v6H4zM13 14h7v6h-7z",
        sort: "M7 5v14M7 19l-3-3M7 19l3-3M17 19V5M17 5l-3 3M17 5l3 3", funnel: "M4 5h16l-6 7v6l-4 2v-8z", plus: "M12 5v14M5 12h14"
    })
    function hOf(m) { return m.kind === "flag" ? 90 : m.kind === "dec" ? 52 : m.kind === "sep" ? 13 : m.kind === "head" ? 30 : 32 }
    function total(list) { var t = 12; for (var i = 0; i < list.length; ++i) t += hOf(list[i]); return t }
    readonly property real menuH: total(v.items)
    readonly property real mx: Math.max(8, Math.min(px, width - 248 - 8))
    readonly property real my: Math.max(8, Math.min(py, height - menuH - 8))
    readonly property real subH: total(v.subItems)
    readonly property real subY: {
        var acc = 0
        for (var i = 0; i < v.items.length; ++i) { if (v.items[i].key === v.sub) break; acc += hOf(v.items[i]) }
        return Math.max(8, Math.min(my + acc, height - subH - 8))
    }
    readonly property real subX: mx + 248 + 4 + 232 + 8 > width ? mx - 232 - 4 : mx + 248 + 4

    function show(kind, row, x, y, sub) {
        px = x; py = y
        if (kind === "photo") ctxMenu.openPhoto(row)
        else ctxMenu.openFolder(UiState.showNames, UiState.showBadges, UiState.dens, UiState.sort, photos.shown < photos.total || UiState.filter !== "all")
        if (sub) ctxMenu.setSub(sub)
    }

    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onPressed: ctxMenu.close() }
    Keys.onEscapePressed: ctxMenu.close()
    onVisibleChanged: if (visible) forceActiveFocus()

    component Panel: Item {
        id: pn
        property var list: []
        property real w: 248
        default property alias content: col.data
        width: w; height: root.total(list)
        scale: root.visible ? 1 : 0.96; transformOrigin: Item.TopLeft
        Behavior on scale { NumberAnimation { duration: 280; easing.type: Easing.OutBack; easing.overshoot: 1.6 } }
        Glass { anchors.fill: parent; sourceItem: root.Window.contentItem; radius: 14; tint: Qt.alpha(theme.s2, 0.92); saturation: 0.4; shadowAlphaA: 0.7; shadowAlphaB: 0; shadowOffsetA: 24; shadowInset: 16 }
        Rectangle { anchors.fill: parent; radius: 14; color: "transparent"; border.color: Qt.alpha(theme.lift, 0.11) }
        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onPressed: (e) => { e.accepted = true } }
        Column { id: col; x: 6; y: 6; width: pn.width - 12 }
    }

    // -------- the menu --------
    Panel {
        x: root.mx; y: root.my; list: root.v.items
        Repeater {
            model: root.v.items
            delegate: Loader {
                id: ld
                required property var modelData
                width: parent ? parent.width : 236
                sourceComponent: modelData.kind === "row" ? rowC : modelData.kind === "sep" ? sepC : modelData.kind === "dec" ? decC : modelData.kind === "flag" ? flagC : null
                onLoaded: item.m = modelData
            }
        }
    }
    // -------- the submenu --------
    Panel {
        visible: root.v.subItems.length > 0
        x: root.subX; y: root.subY; w: 232; list: root.v.subItems
        Repeater {
            model: root.v.subItems
            delegate: Loader {
                required property var modelData
                width: parent ? parent.width : 220
                sourceComponent: modelData.kind === "row" ? rowC : modelData.kind === "sep" ? sepC : modelData.kind === "head" ? headC : null
                onLoaded: { item.m = modelData; if (modelData.kind === "row") item.inSub = true }
            }
        }
    }

    Component { id: sepC; Item { property var m; width: parent ? parent.width : 220; height: 13; Rectangle { x: 4; y: 6; width: parent.width - 8; height: 1; color: theme.lineSoft } } }
    Component { id: headC; Item { property var m; width: parent ? parent.width : 220; height: 30
        Text { x: 10; y: 6; text: m ? m.label.toUpperCase() : ""; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 } } }
    Component {
        id: rowC
        Item {
            id: rw
            property var m
            property bool inSub: false
            width: parent ? parent.width : 220; height: 32
            opacity: m && m.off ? 0.4 : 1
            readonly property bool hot: hh.hovered && !(m && m.off) || (m && m.open === true)
            Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, rw.hot ? 0.08 : 0) }
            // a collection's mark, or an icon
            CollMark { visible: m && m.mark === true; x: 10; anchors.verticalCenter: parent.verticalCenter; color: m && m.color ? m.color : ""; iconPath: m && m.iconPath ? m.iconPath : ""; ring: 12; iconSize: 15 }
            Icon { visible: m && m.mark !== true && m.icon !== undefined && m.icon !== ""; x: 10; anchors.verticalCenter: parent.verticalCenter; size: 16; strokeWidth: 1.7; path: m && m.icon ? (root.paths[m.icon] || "") : ""
                   color: m && m.danger ? theme.rej : (rw.hot ? theme.fg : theme.fg2) }
            Text { x: (m && (m.mark === true || (m.icon !== undefined && m.icon !== ""))) ? 36 : 10; anchors.verticalCenter: parent.verticalCenter; width: parent.width - x - (kb.visible ? kb.width + 16 : 0) - (m && m.arrow ? 24 : 0) - (tk.visible ? 24 : 0)
                   elide: Text.ElideRight; text: m ? m.label : ""; font.pixelSize: 13; color: m && m.danger ? theme.rej : (rw.hot ? theme.fg : theme.fg2) }
            Item { id: kb; visible: m && m.kbd !== undefined && m.kbd !== ""; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; width: kt.implicitWidth + 12; height: 18
                Rectangle { anchors.fill: parent; radius: 5; color: "transparent"; border.color: theme.line }
                Rectangle { x: 1; y: parent.height - 2.5; width: parent.width - 2; height: 1.5; radius: 1; color: theme.line }
                Text { id: kt; anchors.centerIn: parent; anchors.verticalCenterOffset: -0.5; text: m && m.kbd ? m.kbd : ""; color: theme.fg3; font.pixelSize: 10; font.weight: Font.Medium } }
            Icon { visible: m && m.arrow === true; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; name: "chevR"; size: 14; color: theme.fg3 }
            Icon { id: tk; visible: m && m.tick === true; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; name: "check"; size: 14; strokeWidth: 1.9; color: theme.accentFg }
            HoverHandler { id: hh; enabled: !(m && m.off); cursorShape: Qt.PointingHandCursor
                onPointChanged: { if (hovered && m && !rw.inSub) ctxMenu.setSub(m.arrow ? m.key : "") } }
            TapHandler { enabled: !(m && m.off); onTapped: if (m) ctxMenu.act(m.key) }
            Accessible.role: Accessible.MenuItem; Accessible.name: m ? m.label : ""
        }
    }
    Component {
        id: decC
        Item {
            id: dr
            property var m
            width: parent ? parent.width : 236; height: 52
            Row { spacing: 4; width: parent.width
                Repeater { model: dr.m ? dr.m.decs : []
                    delegate: Item { id: db; required property var modelData; width: (dr.width - 8) / 3; height: 50
                        Rectangle { anchors.fill: parent; radius: 10; color: modelData.on ? Qt.tint(theme.base, Qt.alpha(modelData.color, 0.14)) : (dh.hovered ? theme.s3 : Qt.alpha(theme.base, 0.55)); border.width: modelData.on ? 1.5 : 1; border.color: modelData.on ? Qt.alpha(modelData.color, 0.55) : theme.lineSoft }
                        Icon { x: (parent.width - 17) / 2; y: 8; size: 17; strokeWidth: 1.8; path: root.paths[db.modelData.icon]; color: db.modelData.on ? db.modelData.color : (dh.hovered ? theme.fg : theme.fg2) }
                        Text { anchors.horizontalCenter: parent.horizontalCenter; y: 29; text: db.modelData.label; font.pointSize: UiState.pt(11.5); color: db.modelData.on ? db.modelData.color : (dh.hovered ? theme.fg : theme.fg2) }
                        HoverHandler { id: dh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: ctxMenu.act(db.modelData.key) }
                        Accessible.role: Accessible.Button; Accessible.name: modelData.label; Accessible.checked: modelData.on } } }
        }
    }
    Component {
        id: flagC
        Item {
            id: fg
            property var m
            width: parent ? parent.width : 236; height: 90
            Rectangle { x: 0; width: parent.width; height: 86; radius: 10; color: Qt.tint(theme.base, Qt.alpha(fg.m ? fg.m.color : "#888", 0.11)); border.color: Qt.alpha(fg.m ? fg.m.color : "#888", 0.38)
                Row { x: 10; y: 10; spacing: 8; height: 18
                    Rectangle { width: 8; height: 8; radius: 4; color: fg.m ? fg.m.color : "#888"; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: "Flagged: " + (fg.m ? fg.m.label : ""); color: theme.fg; font.pointSize: UiState.pt(12); font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter } }
                Text { x: 10; y: 31; text: "Dimmed in the grid, not removed."; color: theme.fg3; font.pointSize: UiState.pt(12) }
                Row { x: 10; y: 54; spacing: 6
                    SButton { text: "Why was it flagged?"; h: 26; hpad: 10; radius: 8; onClicked: ctxMenu.act("flagwhy") }
                    SButton { text: "Restore"; h: 26; hpad: 10; radius: 8; onClicked: ctxMenu.act("flagrestore") } } }
        }
    }
}
