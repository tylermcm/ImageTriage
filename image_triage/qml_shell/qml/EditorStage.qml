import QtQuick

// PhotoCraft fills the host. Space returns to the grid through EditorHost's
// save/close flow, including when the foreign native window has keyboard focus.
Item {
    id: stage
    objectName: "editorStage"
    anchors.fill: parent
    z: 95
    readonly property bool shown: editor.state === "editing" || editor.state === "closing"
    visible: shown

    Rectangle { anchors.fill: parent; color: theme.base }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; hoverEnabled: true; onWheel: (w) => { w.accepted = true } }   // nothing under the editor sees the pointer
    Item {   // where the editor's window goes
        id: canvas
        anchors.fill: parent
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
