import QtQuick

// A settings button: "normal" (raised, 1px ring), "primary" (filled accent) or "ghost" (text only).
Item {
    id: b
    property string text
    property string variant: "normal"
    property real h: 30
    property real hpad: 12
    property real radius: 10
    signal clicked()
    implicitWidth: label.implicitWidth + 2 * hpad
    implicitHeight: h
    opacity: enabled ? 1 : 0.4
    scale: tap.pressed ? 0.96 : 1
    Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }
    Rectangle {
        anchors.fill: parent; radius: b.radius
        color: b.variant === "primary" ? (hover.hovered ? Qt.tint(theme.accent, Qt.alpha(theme.lift, 0.10)) : theme.accent)
             : b.variant === "ghost" ? Qt.alpha(theme.lift, hover.hovered ? 0.08 : 0)
             : (hover.hovered ? Qt.tint(theme.s3, Qt.alpha(theme.lift, 0.06)) : theme.s3)
        Behavior on color { ColorAnimation { duration: 150 } }
    }
    Rectangle { visible: b.variant === "normal"; anchors.fill: parent; radius: b.radius; color: "transparent"; border.color: theme.lineSoft; border.width: 1 }
    Text {
        id: label; anchors.centerIn: parent; text: b.text; font.pixelSize: 13
        font.weight: b.variant === "primary" ? Font.Medium : Font.Normal
        color: b.variant === "primary" ? theme.onAccent : (b.variant === "ghost" && !hover.hovered ? theme.fg2 : theme.fg)
    }
    HoverHandler { id: hover; enabled: b.enabled; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; enabled: b.enabled; onTapped: b.clicked() }
    Accessible.role: Accessible.Button; Accessible.name: b.text
}
