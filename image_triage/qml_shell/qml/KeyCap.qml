import QtQuick

// A key cap as the help windows show it (the mockup's .kb kbd): 11px, a thicker bottom edge; blue when the user changed the key.
Item {
    id: root
    property string text
    property bool changed: false
    implicitWidth: Math.max(14 + 14, label.implicitWidth + 14); implicitHeight: 21
    width: implicitWidth; height: implicitHeight
    Rectangle { anchors.fill: parent; radius: 6; color: theme.s1; border.color: root.changed ? Qt.tint(theme.line, Qt.alpha(theme.accent, 0.55)) : theme.line }
    Rectangle { x: 1; y: parent.height - 2; width: parent.width - 2; height: 1.5; radius: 1; color: root.changed ? Qt.tint(theme.line, Qt.alpha(theme.accent, 0.55)) : theme.line }
    Text { id: label; anchors.centerIn: parent; anchors.verticalCenterOffset: -0.5; text: root.text; color: root.changed ? theme.accentFg : theme.fg2; font.pointSize: UiState.pt(11); font.weight: Font.Medium }
}
