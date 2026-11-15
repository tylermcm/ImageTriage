import QtQuick

// The Light Depth handles drawn on the photo: position (the centre of the pool of light), size (its radius) and feather
// (how far from the depth the effect takes to come in). The item covers the photo as it is painted; the settings are fractions of it.
Item {
    id: handles
    required property var model     // RawLightDepth
    required property var raw       // RawEditor (gestures: one drag is one undo step)
    property bool suppressed: false   // the picture is not the one the handles belong to (before/after, dropper armed)
    readonly property var values: model ? model.settings : ({x: 0.5, y: 0.5, size: 100, feather: 15})
    readonly property real cx: values.x * width
    readonly property real cy: values.y * height
    // The pool's radius on screen: pool_spread (picture widths) halved so every size can be reached from the middle.
    readonly property real radius: (0.05 + 0.95 * values.size / 100) * width * 0.5
    readonly property bool pooled: values.size < 100
    readonly property int gripSize: 16
    readonly property real trackWidth: Math.min(96, width * 0.4)
    visible: !suppressed && !!model && model.open && model.ready && model.active && model.shown && width > 0 && height > 0
    enabled: !!raw && raw.loaded && !raw.saving

    // Where on the ring the size grip sits: the first side that is inside the photo.
    function ringAngle() {
        var sides = [0, Math.PI, -Math.PI / 2, Math.PI / 2]
        for (var i = 0; i < sides.length; i++) {
            var x = cx + radius * Math.cos(sides[i]), y = cy + radius * Math.sin(sides[i])
            if (x >= gripSize && x <= width - gripSize && y >= gripSize && y <= height - gripSize) return sides[i]
        }
        return 0
    }
    readonly property real angle: ringAngle()

    Rectangle {   // the pool
        objectName: "lightDepthRing"
        visible: handles.pooled
        x: handles.cx - handles.radius; y: handles.cy - handles.radius
        width: handles.radius * 2; height: width; radius: width / 2
        color: "transparent"; border.color: "#ccffffff"; border.width: 1.5
        Rectangle { anchors.fill: parent; anchors.margins: -1; radius: width / 2; color: "transparent"; border.color: "#66000000"; border.width: 1 }
    }

    component Grip: Rectangle {
        id: knob
        property var host: handles
        property string tip: ""
        property alias area: drag
        signal moved(real px, real py)      // the pointer, in this item's own coordinates
        width: host.gripSize + 6; height: width; radius: width / 2
        color: drag.pressed || drag.containsMouse ? "#ffffff" : "#f4f4f4"; border.color: "#202020"; border.width: 1
        Accessible.role: Accessible.Slider; Accessible.name: tip
        MouseArea {
            id: drag
            anchors.fill: parent; anchors.margins: -6
            hoverEnabled: true; preventStealing: true
            cursorShape: Qt.PointingHandCursor
            onPressed: host.raw.beginGesture()
            onPositionChanged: (mouse) => { if (pressed) { var p = mapToItem(host, mouse.x, mouse.y); knob.moved(p.x, p.y) } }
            onReleased: host.raw.endGesture()
            onCanceled: host.raw.endGesture()
        }
    }

    Grip {   // position
        objectName: "lightDepthPosition"
        tip: "Light position"
        x: handles.cx - width / 2; y: handles.cy - height / 2
        onMoved: (px, py) => handles.model.place(Math.max(0, Math.min(1, px / handles.width)), Math.max(0, Math.min(1, py / handles.height)))
        Rectangle { anchors.centerIn: parent; width: 6; height: 6; radius: 3; color: "#202020" }
    }
    Grip {   // size
        objectName: "lightDepthSize"
        tip: "Light size"
        // On the ring; when no side of the ring is inside the photo (a pool as big as the photo) it is held at the edge.
        x: Math.max(handles.gripSize, Math.min(handles.width - handles.gripSize, handles.cx + handles.radius * Math.cos(handles.angle))) - width / 2
        y: Math.max(handles.gripSize, Math.min(handles.height - handles.gripSize, handles.cy + handles.radius * Math.sin(handles.angle))) - height / 2
        onMoved: (px, py) => {
            var distance = Math.hypot(px - handles.cx, py - handles.cy) / (handles.width * 0.5)
            handles.model.adjust("size", Math.max(0, Math.min(100, (distance - 0.05) / 0.95 * 100)))
        }
        Rectangle { anchors.centerIn: parent; width: 8; height: 2; color: "#202020" }
    }
    Item {   // feather: a short track below the position handle
        id: featherTrack
        objectName: "lightDepthFeatherTrack"
        width: handles.trackWidth; height: 22
        x: Math.max(4, Math.min(handles.width - width - 4, handles.cx - width / 2))
        y: Math.min(handles.height - height - 4, handles.cy + 34)
        Rectangle { x: 0; y: parent.height / 2 - 1.5; width: parent.width; height: 3; radius: 1.5; color: "#ccffffff"; border.color: "#66000000"; border.width: 1 }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.top; anchors.bottomMargin: 1
            text: "Feather"; color: "#f4f4f4"; font.pixelSize: 10; style: Text.Outline; styleColor: "#000000"
        }
        Grip {
            objectName: "lightDepthFeather"
            tip: "Feather: how far from the depth the effect takes to come in"
            x: handles.values.feather / 100 * (featherTrack.width - width); y: (featherTrack.height - height) / 2
            onMoved: (px, py) => {
                var local = handles.mapToItem(featherTrack, px, py).x
                handles.model.adjust("feather", Math.max(0, Math.min(100, (local - width / 2) / (featherTrack.width - width) * 100)))
            }
        }
    }
}
