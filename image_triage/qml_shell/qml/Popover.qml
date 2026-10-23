import QtQuick
import QtQuick.Controls

// A menu that scales and fades in beside the control that opened it. A modal Popup, so a click anywhere else (or Escape) closes it
// and the click does not reach what is underneath.
Popup {
    id: pop
    property int placement: 0            // 0: above the button, 1: to the right of it
    property bool alignBottom: false     // placement 1 only: grow upward from the button's bottom edge
    property real minWidth: 220
    property real maxHeight: 10000
    default property alias content: column.data

    modal: true
    dim: false
    focus: true
    padding: 6
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    x: placement === 1 ? parent.width + 10 : 0
    y: placement === 1 ? (alignBottom ? parent.height - height + 4 : -8) : -height - 12

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 170 }
            NumberAnimation { property: "scale"; from: 0.95; to: 1; duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.6 }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; to: 0; duration: 110 }
            NumberAnimation { property: "scale"; to: 0.97; duration: 110 }
        }
    }
    background: Rectangle { radius: 16; color: Qt.alpha(theme.s2, 0.97); border.color: Qt.alpha(theme.lift, 0.11) }
    contentItem: Flickable {
        implicitWidth: Math.max(pop.minWidth, column.implicitWidth)
        implicitHeight: Math.min(pop.maxHeight, column.implicitHeight)
        contentHeight: column.implicitHeight
        interactive: contentHeight > height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        Column { id: column; width: parent.width }
    }
}
