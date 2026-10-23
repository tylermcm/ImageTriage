import QtQuick

// A small label that slides out beside a button after a short hover.
Rectangle {
    id: tip
    property string text
    property bool show: false
    property bool above: false
    visible: opacity > 0.01
    opacity: show ? 1 : 0
    z: 100
    x: above ? (parent.width - width) / 2 : parent.width + 10 - (show ? 0 : 4)
    y: above ? -height - 8 : (parent.height - height) / 2
    width: label.implicitWidth + 18
    height: 26
    radius: 8
    color: theme.s3
    border.color: theme.line
    Behavior on opacity { NumberAnimation { duration: 140 } }
    Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    Text { id: label; anchors.centerIn: parent; text: tip.text; color: theme.fg; font.pixelSize: 12; font.weight: Font.Medium }
}
