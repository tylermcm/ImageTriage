import QtQuick
import QtQuick.Controls

// A menu that scales and fades in beside the control that opened it. A modal Popup, so a click anywhere else (or Escape) closes it
// and the click does not reach what is underneath.
Popup {
    id: pop
    property int placement: 0            // 0: above the button, 1: to the right of it
    property bool alignBottom: false     // placement 1 only: grow upward from the button's bottom edge
    property string name: ""            // the id UiState.menu uses for this menu (one menu open at a time)
    readonly property bool wanted: name !== "" && UiState.menu === name
    onWantedChanged: { if (wanted) open(); else if (opened) close() }
    onClosed: { if (name !== "" && UiState.menu === name) UiState.menu = "" }
    Component.onCompleted: { if (wanted) open() }
    property real minWidth: 194             // min-width 208 less padding and border
    property real maxHeight: 10000
    default property alias content: column.data

    modal: true
    dim: false
    focus: true
    padding: 7                           // 6 + the 1px border
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    // compact (<= 900px): the bar's menus become a sheet 12px from both sides, its bottom edge 76px above the window's
    readonly property bool sheet: UiState.compact && placement === 0
    property real originX: 0
    property real originY: 0
    onAboutToShow: { var o = parent.mapToItem(null, 0, 0); originX = o.x; originY = o.y }
    x: sheet ? 12 - originX : (placement === 1 ? parent.width + 10 : 0)
    y: sheet ? parent.Window.height - 76 - height - originY : (placement === 1 ? (alignBottom ? parent.height - height + 4 : -8) : -height - 12)

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
    // the mockup's .pop: backdrop blur(22px) saturate(1.4) under an 88% tint, shadow 0 24px 48px -16px rgba(0,0,0,.7)
    background: Glass {
        sourceItem: pop.parent ? pop.parent.Window.contentItem : null
        radius: 16; tint: Qt.alpha(theme.s2, 0.88); saturation: 0.4
        shadowAlphaA: 0.7; shadowAlphaB: 0; shadowOffsetA: 24; shadowInset: 16
    }
    contentItem: Flickable {
        implicitWidth: pop.sheet ? pop.parent.Window.width - 24 - 14 : Math.max(pop.minWidth, column.implicitWidth)
        implicitHeight: Math.min(pop.maxHeight, column.implicitHeight)
        contentHeight: column.implicitHeight
        interactive: contentHeight > height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        Column { id: column; width: parent.width }
    }
}
