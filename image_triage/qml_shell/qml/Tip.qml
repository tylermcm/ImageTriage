import QtQuick

// A small label that slides out beside a button after a short hover.
Rectangle {
    id: tip
    property string text
    property bool show: false
    property bool above: false
    property bool leftSide: false        // to the left of the control (for one at the right edge of a menu, where a tip below would sit behind the next row)
    property bool below: false           // centred under the control, for the ones in a bar along the top
    visible: opacity > 0.01
    opacity: show ? 1 : 0
    z: 100
    x: leftSide ? -width - 8 + (show ? 0 : 4) : (above || below ? (parent.width - width) / 2 : parent.width + 10 - (show ? 0 : 4))
    y: above ? -height - 8 : (below ? parent.height + 6 : (parent.height - height) / 2)
    width: label.implicitWidth + 18
    height: 26
    radius: 8
    color: theme.s3
    border.color: theme.line
    Behavior on opacity { NumberAnimation { duration: 140 } }
    Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    Text { id: label; anchors.centerIn: parent; text: tip.text; color: theme.fg; font.pixelSize: 12; font.weight: Font.Medium }
}
