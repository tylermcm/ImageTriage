import QtQuick

Item {
    id: c
    property string label
    property string iconPath
    property bool done: false
    signal clicked()
    width: row.width + 20; height: 28
    Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(c.done ? theme.win : theme.accent, hov.hovered && !c.done ? 0.26 : 0.16) }
    Row {
        id: row; anchors.centerIn: parent; spacing: 6
        PdIcon { path: c.done ? "M4.5 12.5l4.5 4.5 10.5-10.5" : c.iconPath; size: 14; strokeWidth: 2.2; color: c.done ? theme.win : theme.accentFg; anchors.verticalCenter: parent.verticalCenter }
        Text { text: c.label; color: c.done ? theme.win : theme.accentFg; font.pixelSize: 12; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter }
    }
    HoverHandler { id: hov; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: c.clicked() }
    Accessible.role: Accessible.Button; Accessible.name: c.label
}
