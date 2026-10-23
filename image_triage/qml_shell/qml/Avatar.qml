import QtQuick
import QtQuick.Shapes

// A person on the Faces page: a conic-gradient disc with an initial, a name and a photo count.
Item {
    id: av
    property string initial
    property string label
    property int count
    property real hue: 200
    width: 84; height: 108
    readonly property color c1: Qt.hsla(hue / 360, 0.55, 0.52, 1)
    readonly property color c2: Qt.hsla(((hue + 40) % 360) / 360, 0.6, 0.62, 1)
    Item {
        id: disc
        anchors.horizontalCenter: parent.horizontalCenter; width: 64; height: 64
        scale: hover.hovered ? 1.07 : 1
        Behavior on scale { SpringAnimation { spring: 5; damping: 0.35; epsilon: 0.001 } }
        Shape {
            anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: theme.line; strokeWidth: 1
                fillGradient: ConicalGradient {
                    centerX: 32; centerY: 32; angle: av.hue
                    GradientStop { position: 0.0; color: av.c1 }
                    GradientStop { position: 0.5; color: av.c2 }
                    GradientStop { position: 1.0; color: av.c1 }
                }
                PathSvg { path: Icons.circle(32, 32, 31.5) }
            }
        }
        Text { anchors.centerIn: parent; text: av.initial; color: "white"; font.pixelSize: 18; font.weight: Font.DemiBold }
    }
    Text { y: 70; width: parent.width; horizontalAlignment: Text.AlignHCenter; text: av.label; color: theme.fg2; font.pixelSize: 12; elide: Text.ElideRight }
    Text { y: 88; width: parent.width; horizontalAlignment: Text.AlignHCenter; text: av.count + " photos"; color: theme.fg3; font.pixelSize: 11 }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
}
