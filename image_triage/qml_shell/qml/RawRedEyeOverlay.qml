import QtQuick

Item {
    id: overlay
    required property var model
    required property var raw
    required property Item viewport
    property bool showOverlay: true
    readonly property var crop: raw.values.hostCrop
    readonly property real radius: 2*Math.max(8, 4+1.2*model.pupilSize)
    readonly property real diameter: visible ? Math.max(12, radius*width/Math.max(1, raw.originalWidth)/crop.w) : 0

    function point(p) { return Qt.point((p[0]-crop.x)/crop.w*width, (p[1]-crop.y)/crop.h*height) }
    function accepts(x, y) {
        if (x < 0 || y < 0 || x >= width || y >= height) return false
        var p = mapToItem(viewport, x, y)
        return p.x >= 0 && p.y >= 0 && p.x < viewport.width && p.y < viewport.height
    }
    Repeater {
        model: overlay.showOverlay ? overlay.model.rows : []
        Rectangle {
            required property var modelData
            required property int index
            x: overlay.point(modelData.point).x-width/2; y: overlay.point(modelData.point).y-height/2
            width: 16; height: 16; radius: 8; color: "transparent"
            border.width: overlay.model.selected === index ? 2 : 1
            border.color: overlay.model.selected === index ? theme.accent : "white"
        }
    }
    Rectangle {
        objectName: "rawRedEyeCursor"
        visible: pointer.enabled && pointer.containsMouse && overlay.accepts(pointer.mouseX, pointer.mouseY)
        x: pointer.mouseX-width/2; y: pointer.mouseY-height/2
        width: overlay.diameter; height: width; radius: width/2; color: "transparent"; border.color: "white"
    }
    MouseArea {
        id: pointer; objectName: "rawRedEyeInput"
        anchors.fill: parent; hoverEnabled: true; preventStealing: true
        acceptedButtons: Qt.LeftButton; cursorShape: Qt.CrossCursor
        enabled: overlay.raw.loaded && !overlay.raw.saving && !overlay.raw.busy
        onPressed: (event) => { if (!overlay.accepts(event.x, event.y)) event.accepted = false }
        onClicked: (event) => {
            if (!overlay.accepts(event.x, event.y)) return
            var rows = overlay.model.rows
            for (var i=rows.length-1; i>=0; --i) {
                var p = overlay.point(rows[i].point)
                if (Math.hypot(event.x-p.x, event.y-p.y) <= 8) { overlay.model.select(i); return }
            }
            overlay.model.correct(event.x/width, event.y/height)
        }
    }
}
