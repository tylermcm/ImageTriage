import QtQuick
import QtQuick.Shapes

// A person on the Faces page: a conic-gradient disc (OKLCH ramp from the hue) with an initial, a name and a photo count.
// The mockup's button keeps the browser's 1px 6px padding, so the cell is 84 x 111.6.
Item {
    id: av
    property string initial
    property string label
    property int count
    property real hue: 200
    width: 84; height: 111.6
    readonly property color c1: theme.oklch(0.62, 0.14, hue)
    readonly property color c2: theme.oklch(0.74, 0.12, hue + 40)
    Item {
        id: disc
        x: 10; y: 1; width: 64; height: 64
        scale: hover.hovered ? 1.07 : 1
        Behavior on scale { SpringAnimation { spring: 5; damping: 0.35; epsilon: 0.001 } }
        Rectangle { x: -3; y: -3; width: 70; height: 70; radius: 35; color: theme.line }     // 0 0 0 3px line
        Rectangle { x: -2; y: -2; width: 68; height: 68; radius: 34; color: theme.s2 }       // 0 0 0 2px s2
        Shape {
            anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: ConicalGradient {
                    centerX: 32; centerY: 32; angle: 90 - av.hue      // CSS: clockwise from `hue` deg; the ramp is symmetric, so only the start matters
                    GradientStop { position: 0.0; color: av.c1 }
                    GradientStop { position: 0.5; color: av.c2 }
                    GradientStop { position: 1.0; color: av.c1 }
                }
                PathSvg { path: Icons.circle(32, 32, 32) }
            }
        }
        Text { anchors.fill: parent; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; text: av.initial; color: "white"; font.pixelSize: 18; font.weight: Font.DemiBold }
    }
    Text { x: 6; y: 71; width: parent.width - 12; height: 18.2; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter; text: av.label; color: theme.fg; font.pixelSize: 13; elide: Text.ElideRight }
    Text { x: 6; y: 95.2; width: parent.width - 12; height: 15.4; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter; text: av.count + " photos"; color: theme.fg3; font.pixelSize: 11 }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
}
