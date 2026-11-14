import QtQuick

Item {
    id: s
    property bool checked: false
    signal toggled()
    width: 40; height: 24
    Rectangle { anchors.fill: parent; radius: 12; color: s.checked ? theme.accent : theme.s3; border.color: s.checked ? "transparent" : theme.line
        Behavior on color { ColorAnimation { duration: 120 } } }
    Rectangle { y: 3; x: s.checked ? s.width - 21 : 3; width: 18; height: 18; radius: 9; color: s.checked ? theme.onAccent : theme.fg2
        Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } } }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: s.toggled() }
    Accessible.role: Accessible.CheckBox; Accessible.checked: s.checked
}
