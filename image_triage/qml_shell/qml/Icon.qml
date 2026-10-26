import QtQuick
import QtQuick.Shapes

Item {
    id: root
    property string name
    property color color: "white"
    property real size: 18
    property real strokeWidth: 1.6
    property bool filled: false
    property string path: ""             // a raw SVG path (24x24 box) instead of a named icon
    width: size
    height: size
    Shape {
        width: 24; height: 24
        readonly property var ink: root.path === "" && Icons.inkOffset[root.name] ? Icons.inkOffset[root.name] : [0, 0]
        x: -ink[0] * root.size / 24; y: -ink[1] * root.size / 24
        scale: root.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: root.color
            strokeWidth: root.strokeWidth
            fillColor: root.filled ? root.color : "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: root.path !== "" ? root.path : (Icons.paths[root.name] || "") }
        }
    }
}
