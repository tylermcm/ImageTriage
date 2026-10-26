import QtQuick

// Content that rises into place, a beat after its neighbours, whenever `active` turns on (the rows of a side-panel page).
Item {
    id: stag
    property bool active: true
    property int n: 0
    default property alias content: holder.data
    implicitWidth: holder.childrenRect.width
    implicitHeight: holder.childrenRect.height
    width: parent ? parent.width : implicitWidth
    height: implicitHeight
    onActiveChanged: if (active) { holder.opacity = 0; lift.y = 8; anim.restart() }
    Item {
        id: holder
        width: parent.width; height: childrenRect.height
        transform: Translate { id: lift; y: 0 }
    }
    SequentialAnimation {
        id: anim
        PauseAnimation { duration: 40 + stag.n * 40 }
        ParallelAnimation {
            NumberAnimation { target: holder; property: "opacity"; to: 1; duration: 420; easing.type: Easing.OutCubic }
            NumberAnimation { target: lift; property: "y"; to: 0; duration: 480; easing.type: Easing.OutCubic }
        }
    }
}
