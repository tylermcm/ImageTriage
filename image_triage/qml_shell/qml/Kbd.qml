import QtQuick

// A key cap, as the mockup's kbd: 10px medium text, 1px outline, thicker bottom edge.
Item {
    id: root
    property string text
    property real size: 10
    implicitWidth: label.implicitWidth + 12
    implicitHeight: 18
    width: implicitWidth; height: implicitHeight
    Rectangle { anchors.fill: parent; radius: 5; color: "transparent"; border.color: theme.line }
    Rectangle { x: 1; y: parent.height - 2; width: parent.width - 2; height: 1.5; radius: 1; color: theme.line }
    Text {
        id: label
        anchors.centerIn: parent; anchors.verticalCenterOffset: 0
        text: root.text; color: theme.fg3; font.pointSize: UiState.pt(root.size); font.weight: Font.Medium
    }
}
