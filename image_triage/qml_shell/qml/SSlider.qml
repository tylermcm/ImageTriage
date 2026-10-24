import QtQuick
import QtQuick.Controls

// 220px track (4px, accent fill) with a 16px white thumb that grows a little on hover, and the value to its right.
Item {
    id: sl
    property real from: 0
    property real to: 100
    property real step: 1
    property real trackW: 220
    property real value: 0
    property string text
    signal moved(real value)
    width: trackW + 14 + 52; height: 20
    Slider {
        id: s
        x: 0; width: sl.trackW; height: 20; padding: 0
        from: sl.from; to: sl.to; stepSize: sl.step; snapMode: Slider.SnapAlways
        value: sl.value
        onMoved: sl.moved(value)
        background: Item {
            Rectangle { x: 0; y: 8; width: parent.width; height: 4; radius: 2; color: theme.s3 }
            Rectangle { x: 0; y: 8; width: s.visualPosition * parent.width; height: 4; radius: 2; color: theme.accent }
        }
        handle: Item {
            x: s.visualPosition * (s.width - 16); y: 2; width: 16; height: 16
            scale: s.hovered || s.pressed ? 1.15 : 1
            Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }
            Rectangle { x: 0; y: 2; width: 16; height: 16; radius: 8; color: Qt.rgba(0, 0, 0, 0.30) }
            Rectangle { width: 16; height: 16; radius: 8; color: "#ffffff"; border.color: Qt.rgba(0, 0, 0, 0.25); border.width: 1 }
        }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }
    Text { x: sl.trackW + 14; width: 52; height: 20; horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter; text: sl.text; color: theme.fg2; font.pixelSize: 13 }
}
