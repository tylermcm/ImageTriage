import QtQuick

// Accent swatches: 24px discs; the chosen one gets a 2px gap and a 2px ring in the secondary text colour.
Row {
    id: sw
    property var hues: []
    property int current: -1
    signal picked(int index)
    spacing: 10
    Repeater {
        model: sw.hues
        delegate: Item {
            required property int index
            required property var modelData
            width: 24; height: 24
            scale: h.hovered ? 1.12 : 1
            Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }
            Rectangle { visible: sw.current === index; x: -4; y: -4; width: 32; height: 32; radius: 16; color: theme.fg2 }
            Rectangle { visible: sw.current === index; x: -2; y: -2; width: 28; height: 28; radius: 14; color: theme.base }
            Rectangle {
                anchors.fill: parent; radius: 12
                color: theme.light ? theme.oklch(0.56, 0.17, modelData[1]) : theme.oklch(0.70, 0.15, modelData[1])
            }
            Rectangle { anchors.fill: parent; radius: 12; color: "transparent"; border.color: Qt.rgba(1, 1, 1, 0.18); border.width: 1 }
            HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: sw.picked(index) }
            Accessible.role: Accessible.Button; Accessible.name: modelData[0]
        }
    }
}
