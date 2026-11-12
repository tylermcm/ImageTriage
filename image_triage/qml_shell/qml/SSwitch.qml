import QtQuick

// 38 x 22 switch: off is the raised surface with a 1px ring and a grey knob; on is the accent at 62% with a white knob that springs across.
Item {
    id: sw
    property bool checked: false
    property string name: ""            // what the switch turns on, for a screen reader (the label beside it)
    signal toggled(bool value)
    width: 38; height: 22
    Rectangle {
        anchors.fill: parent; radius: 11
        color: sw.checked ? Qt.alpha(theme.accent, 0.62) : theme.s3
        Behavior on color { ColorAnimation { duration: 250 } }
    }
    Rectangle {
        anchors.fill: parent; radius: 11; color: "transparent"; border.width: 1
        border.color: sw.checked ? Qt.alpha(theme.accent, 0.50) : theme.lineSoft
    }
    Rectangle { x: knob.x; y: knob.y + 1; width: 16; height: 16; radius: 8; color: Qt.rgba(0, 0, 0, 0.35); opacity: 0.6 }
    Rectangle {
        id: knob; y: 3; width: 16; height: 16; radius: 8
        x: sw.checked ? 19 : 3
        color: sw.checked ? "#ffffff" : theme.fg2
        Behavior on x { SpringAnimation { spring: 5.5; damping: 0.38; epsilon: 0.1 } }
        Behavior on color { ColorAnimation { duration: 200 } }
    }
    Rectangle { visible: sw.activeFocus; anchors.fill: parent; anchors.margins: -3; radius: 14; color: "transparent"; border.width: 2; border.color: theme.accent }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: sw.toggled(!sw.checked) }
    activeFocusOnTab: true
    Keys.onPressed: (e) => { if ((e.key === Qt.Key_Space || e.key === Qt.Key_Return || e.key === Qt.Key_Enter) && !e.isAutoRepeat) { sw.toggled(!sw.checked); e.accepted = true } }
    Accessible.role: Accessible.CheckBox; Accessible.checked: sw.checked; Accessible.name: sw.name
}
