import QtQuick

// A drag handle on the edge of the side panel or the inspector. While the pointer is down it reports the edge's x in its parent's
// coordinates; the owner turns that into a width (and clamps it). A double click puts the width back to its default.
Item {
    id: r
    property bool active: drag.active
    signal moved(real x)
    signal finished()
    signal reset()
    width: 9
    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter; width: 2; height: parent.height; radius: 1
        color: theme.accent; opacity: r.active ? 0.9 : hover.hovered ? 0.55 : 0
        Behavior on opacity { NumberAnimation { duration: 140 } }
    }
    HoverHandler { id: hover; cursorShape: Qt.SplitHCursor }
    DragHandler {
        id: drag
        target: null; cursorShape: Qt.SplitHCursor; xAxis.enabled: true; yAxis.enabled: false
        onCentroidChanged: if (active) r.moved(r.parent.mapFromItem(null, centroid.scenePosition.x, 0).x)
        onActiveChanged: if (!active) r.finished()
    }
    TapHandler { acceptedButtons: Qt.LeftButton; gesturePolicy: TapHandler.ReleaseWithinBounds; onDoubleTapped: r.reset() }
}
