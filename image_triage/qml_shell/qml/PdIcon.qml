import QtQuick
import QtQuick.Shapes

// A stroked icon from an SVG path drawn on a 24-unit grid.
Item {
    id: icon
    property string path
    property real size: 16
    property real strokeWidth: 1.8     // in grid units
    property color color: "white"
    implicitWidth: size; implicitHeight: size; width: size; height: size
    Shape {
        width: 24; height: 24; scale: icon.size / 24; transformOrigin: Item.TopLeft
        antialiasing: true
        ShapePath {
            strokeColor: icon.color; strokeWidth: icon.strokeWidth; fillColor: "transparent"
            capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
            PathSvg { path: icon.path }
        }
    }
}
