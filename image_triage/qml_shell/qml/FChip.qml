import QtQuick

// A removable filter chip.
Rectangle {
    id: c
    property string text
    signal removed()
    height: 24; width: label.implicitWidth + 10 + 4 + 18 + 6; radius: 12
    color: Qt.alpha(theme.accent, 0.15); border.width: 1; border.color: Qt.alpha(theme.accent, 0.28)
    Text { id: label; x: 10; anchors.verticalCenter: parent.verticalCenter; text: c.text; color: theme.accentFg; font.pixelSize: 12 }
    Rectangle {
        x: parent.width - 4 - 18; anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18; radius: 9
        color: Qt.alpha(theme.accent, xh.hovered ? 0.25 : 0)
        Icon { anchors.centerIn: parent; size: 10; strokeWidth: 1.8; color: theme.accentFg; path: "M6 6l12 12M18 6L6 18" }
        HoverHandler { id: xh; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: c.removed() }
        Accessible.role: Accessible.Button; Accessible.name: "Remove " + c.text
    }
}
