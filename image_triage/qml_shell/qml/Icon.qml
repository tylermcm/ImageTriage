import QtQuick
import QtQuick.Shapes

Item {
    id: root
    property string name
    property color color: "white"
    property real size: 18
    property real strokeWidth: 1.6
    property bool filled: false
    width: size
    height: size
    Shape {
        width: 24; height: 24
        scale: root.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: root.color
            strokeWidth: root.strokeWidth
            fillColor: root.filled ? root.color : "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: Icons.paths[root.name] || "" }
        }
    }
}
