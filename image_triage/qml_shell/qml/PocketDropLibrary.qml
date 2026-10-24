import QtQuick

// "Library" in PocketDrop's button row: what Image Triage can share, as one row each (the current selection, the winners, collections).
// Folder… stays here for sharing a folder from disk.
Popover {
    id: lib
    property Item panel
    property real panelW: 300
    name: "pdlib"
    placement: 0
    tintAlpha: 0.94
    onAboutToShow: pocketDrop.refreshLibrary(UiState.selRow)
    minWidth: panelW - 24 - 14
    maxHeight: parent && parent.parent ? parent.parent.height - 120 : 400
    x: -4
    y: -8 - height
    readonly property var data: pocketDrop.library

    component Entry: Item {
        id: e
        required property var entry
        width: parent ? parent.width : 200; height: 40
        opacity: entry.empty ? 0.45 : 1
        Rectangle { anchors.fill: parent; radius: 10; color: Qt.alpha(theme.lift, eh.hovered && !entry.empty ? 0.08 : 0) }
        Item { x: 8; y: 5; width: 30; height: 30
            Rectangle { visible: !e.entry.key.startsWith("col:"); anchors.fill: parent; radius: 9; color: theme.s3 }
            Icon { visible: !e.entry.key.startsWith("col:"); anchors.centerIn: parent; size: 16; strokeWidth: 1.7; color: theme.fg2
                path: e.entry.key === "win" ? Icons.star : e.entry.key === "keep" ? "M5 12.5l4.5 4.5L19 7.5" : "M4.5 12.5l4.5 4.5 10.5-10.5" }
        }
        Item { x: 8; y: 5; width: 54; height: 30; visible: e.entry.key.startsWith("col:")
            Repeater { model: 3
                delegate: Item { required property int index; x: index * 16; width: 30; height: 30; visible: true
                    Rectangle { anchors.fill: parent; radius: 8; color: theme.s2 }
                    RoundClip { x: 2; y: 2; width: 26; height: 26; radius: 6
                        Rectangle { anchors.fill: parent; color: theme.s3 }
                        ThumbImage { anchors.fill: parent; bucket: 160; path: e.entry.thumbs[index] || "" } } } } }
        Column { x: 8 + (e.entry.key.startsWith("col:") ? 54 : 30) + 10; anchors.verticalCenter: parent.verticalCenter; width: parent.width - x - 50
            Text { width: parent.width; elide: Text.ElideRight; text: e.entry.title; color: theme.fg; font.pixelSize: 13; height: 16; verticalAlignment: Text.AlignVCenter }
            Text { width: parent.width; elide: Text.ElideRight; text: e.entry.sub; color: theme.fg3; font.pointSize: UiState.pt(11.5); height: 16; verticalAlignment: Text.AlignVCenter } }
        Text { anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; visible: e.entry.added; text: "Added"; color: theme.win; font.pointSize: UiState.pt(11) }
        HoverHandler { id: eh; cursorShape: e.entry.empty ? Qt.ArrowCursor : Qt.PointingHandCursor }
        TapHandler { enabled: !e.entry.empty; onTapped: { pocketDrop.addLibrary(e.entry.key); lib.close() } }
        Accessible.role: Accessible.Button; Accessible.name: e.entry.title + ", " + e.entry.sub
    }

    PopHeader { caption: "Current" }
    Repeater { model: lib.data.current || []; delegate: Entry { required property var modelData; entry: modelData } }
    PopDivider {}
    PopHeader { caption: "Collections" }
    Repeater { model: lib.data.collections || []; delegate: Entry { required property var modelData; entry: modelData } }
    PopDivider {}
    Item {
        width: parent.width; height: 40
        Rectangle { anchors.fill: parent; radius: 10; color: Qt.alpha(theme.lift, fh.hovered ? 0.08 : 0) }
        Rectangle { x: 8; y: 5; width: 30; height: 30; radius: 9; color: theme.s3
            Icon { anchors.centerIn: parent; name: "folder"; size: 16; strokeWidth: 1.7; color: theme.fg2 } }
        Column { x: 48; anchors.verticalCenter: parent.verticalCenter
            Text { text: "Folder…"; color: theme.fg; font.pixelSize: 13; height: 16; verticalAlignment: Text.AlignVCenter }
            Text { text: "Share a folder from disk"; color: theme.fg3; font.pointSize: UiState.pt(11.5); height: 16; verticalAlignment: Text.AlignVCenter } }
        HoverHandler { id: fh; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: { lib.close(); panel.chooseFolder() } }
        Accessible.role: Accessible.Button; Accessible.name: "Folder: share a folder from disk"
    }
}
