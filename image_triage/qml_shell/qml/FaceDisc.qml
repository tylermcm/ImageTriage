import QtQuick
import QtQuick.Shapes

// A person's stand-in portrait: a conic-gradient disc (the OKLCH ramp from `hue`) with the initial, or a "?" when unnamed.
Item {
    id: d
    property real hue: 200
    property string initial: "?"
    property real fontSize: 13
    readonly property color c1: theme.oklch(0.62, 0.14, hue)
    readonly property color c2: theme.oklch(0.74, 0.12, hue + 40)
    Shape {
        anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: ConicalGradient {
                centerX: d.width / 2; centerY: d.height / 2; angle: 90 - d.hue
                GradientStop { position: 0.0; color: d.c1 }
                GradientStop { position: 0.5; color: d.c2 }
                GradientStop { position: 1.0; color: d.c1 }
            }
            PathSvg { path: Icons.circle(d.width / 2, d.height / 2, d.width / 2) }
        }
    }
    Text { anchors.fill: parent; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; text: d.initial; color: "white"; font.pixelSize: d.fontSize; font.weight: Font.DemiBold }
}
