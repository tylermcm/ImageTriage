import QtQuick

// A button on the floating bar: icon, optional label, optional caret that flips while its menu is open.
Item {
    id: btn
    property string icon
    property string label
    property bool showLabel: true
    property bool caret: false
    property bool open: false
    property string dragId: icon        // the bar's buttons can be dragged into another order (DragReorder)
    default property alias menuContent: holder.data
    signal clicked()
    implicitHeight: 36
    implicitWidth: row.implicitWidth + 2 * (UiState.compact ? 9 : (showLabel && label !== "" ? 11 : 10))

    Rectangle {
        anchors.fill: parent; radius: 14
        color: btn.open ? Qt.alpha(theme.accent, 0.16) : Qt.alpha(theme.lift, hover.hovered ? 0.08 : 0)
        Behavior on color { ColorAnimation { duration: 140 } }
    }
    scale: press.pressed ? 0.96 : 1
    Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Icon { name: btn.icon; size: 17; color: btn.open ? theme.accentFg : theme.fg; anchors.verticalCenter: parent.verticalCenter }
        Text { visible: btn.showLabel && btn.label !== ""; text: btn.label; font.pixelSize: 13; color: btn.open ? theme.accentFg : theme.fg; anchors.verticalCenter: parent.verticalCenter }
        Icon {
            visible: btn.caret; name: "chevUp"; size: 12; color: btn.open ? theme.accentFg : theme.fg; opacity: 0.6; anchors.verticalCenter: parent.verticalCenter
            rotation: btn.open ? 180 : 0
            Behavior on rotation { SpringAnimation { spring: 6; damping: 0.45; epsilon: 0.5 } }
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: press; onTapped: btn.clicked() }
    Item { id: holder; anchors.fill: parent; DragReorder { horizontal: true; orderName: "bar" } }
    Accessible.role: Accessible.Button
    Accessible.name: btn.label !== "" ? btn.label : btn.icon
}
