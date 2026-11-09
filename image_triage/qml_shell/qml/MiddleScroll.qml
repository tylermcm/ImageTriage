import QtQuick
import QtQuick.Window

// Wheel scrolling and Windows-style middle-button autoscroll for a Flickable.
Item {
    id: root
    required property Flickable view
    property bool active: false
    property point origin: Qt.point(0, 0)
    property real pointerY: 0
    readonly property var cursor: typeof nativeScrollCursor !== "undefined" ? nativeScrollCursor : null
    function stop() { active = false }
    function clampY(y) { return Math.max(view.originY, Math.min(Math.max(view.originY, view.originY + view.contentHeight - view.height), y)) }
    onActiveChanged: {
        if (cursor) { if (active) cursor.begin(); else cursor.end() }
    }
    onPointerYChanged: if (active && cursor) cursor.direction(Math.abs(pointerY - origin.y) <= 16 ? 0 : Math.sign(pointerY - origin.y))
    onVisibleChanged: if (!visible) stop()
    onEnabledChanged: if (!enabled) stop()
    Component.onDestruction: if (cursor && active) cursor.end()
    Connections { target: root.Window.window; function onActiveChanged() { if (!root.Window.active) root.stop() } }
    Keys.onEscapePressed: (event) => { if (active) { stop(); event.accepted = true } else event.accepted = false }
    NumberAnimation { id: ease; target: root.view; property: "contentY"; duration: 100; easing.type: Easing.OutCubic }
    WheelHandler {
        target: null
        onWheel: (event) => {
            root.stop()
            var from = ease.running ? ease.to : root.view.contentY
            var delta = event.pixelDelta.y !== 0 ? event.pixelDelta.y : event.angleDelta.y / 120 * 200
            ease.stop(); root.view.cancelFlick()
            ease.from = root.view.contentY; ease.to = root.clampY(from - delta); ease.start()
            event.accepted = true
        }
    }
    MouseArea {
        anchors.fill: parent
        acceptedButtons: root.active ? Qt.AllButtons : Qt.MiddleButton
        hoverEnabled: true; preventStealing: true
        property bool moved: false
        property bool stopping: false
        onPressed: (event) => {
            stopping = root.active
            if (stopping) return
            if (event.button !== Qt.MiddleButton) { event.accepted = false; return }
            ease.stop(); root.view.cancelFlick(); root.forceActiveFocus()
            root.origin = Qt.point(event.x, event.y); root.pointerY = event.y
            moved = false; root.active = true
        }
        onPositionChanged: (event) => {
            root.pointerY = event.y
            if (Math.abs(event.x - root.origin.x) + Math.abs(event.y - root.origin.y) >= 10) moved = true
        }
        onReleased: if (stopping || moved) root.stop()
        onCanceled: root.stop()
        onExited: if (!pressed) root.stop()
        onWheel: (event) => { event.accepted = false }
        TapHandler { enabled: root.active; acceptedButtons: Qt.AllButtons; gesturePolicy: TapHandler.WithinBounds }
    }
    Timer {
        interval: 16; repeat: true; running: root.active
        onTriggered: {
            var delta = root.pointerY - root.origin.y, magnitude = Math.abs(delta) - 16
            if (magnitude > 0) root.view.contentY = root.clampY(root.view.contentY + Math.sign(delta) * Math.min(130, magnitude / 9 * (1 + magnitude / 140)))
        }
    }
    Image {
        visible: root.active && source.toString() !== ""
        source: root.cursor ? root.cursor.anchorSource : ""
        width: root.cursor ? root.cursor.anchorWidth : 0; height: root.cursor ? root.cursor.anchorHeight : 0
        x: root.origin.x - width / 2; y: root.origin.y - height / 2
    }
}
