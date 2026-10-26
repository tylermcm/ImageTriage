import QtQuick

// The hosted PhotoCraft editor: a bar of ours across the top and, below it, the editor's own native window, which Python (editor_host.py)
// puts inside this window. A native window is drawn over QML, so nothing of ours can sit on top of the editor; this item only reserves its
// place and tells Python where it is.
Item {
    id: stage
    objectName: "editorStage"
    anchors.fill: parent
    z: 95
    readonly property bool shown: editor.state === "editing" || editor.state === "closing"
    visible: shown
    readonly property int barH: 44
    readonly property string name: { var p = editor.path; var i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\")); return i >= 0 ? p.substring(i + 1) : p }

    Rectangle { anchors.fill: parent; color: theme.base }
    Rectangle {
        id: top
        width: parent.width; height: stage.barH; color: theme.s1
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
        Rectangle {
            x: 10; anchors.verticalCenter: parent.verticalCenter; height: 30; width: backRow.implicitWidth + 22; radius: 9
            color: backHover.hovered ? theme.s3 : theme.s2; border.color: theme.lineSoft
            Row { id: backRow; x: 11; anchors.verticalCenter: parent.verticalCenter; spacing: 7
                Icon { anchors.verticalCenter: parent.verticalCenter; path: "M15 5l-7 7 7 7"; size: 15; color: theme.fg2 }
                Text { anchors.verticalCenter: parent.verticalCenter; text: "Back to photo"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium } }
            HoverHandler { id: backHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: editor.close() }
            Accessible.role: Accessible.Button; Accessible.name: "Back to photo"
        }
        Text { anchors.centerIn: parent; text: stage.name; color: theme.fg2; font.pixelSize: 13; elide: Text.ElideMiddle; width: Math.min(implicitWidth, parent.width - 320) }
        Text { visible: editor.state === "closing"; anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: "Saving…"; color: theme.fg3; font.pixelSize: 12 }
    }
    Item {   // where the editor's window goes
        id: canvas
        y: stage.barH; width: parent.width; height: parent.height - stage.barH
        function report() {
            if (!stage.visible) return
            var p = canvas.mapToItem(null, 0, 0), d = Screen.devicePixelRatio
            editor.setGeometry(Math.round(p.x * d), Math.round(p.y * d), Math.round(width * d), Math.round(height * d))
        }
        onWidthChanged: report()
        onHeightChanged: report()
        onYChanged: report()
        Connections { target: stage; function onVisibleChanged() { canvas.report() } }
        Component.onCompleted: report()
    }
}
