import QtQuick
import QtQuick.Shapes

// How a collection is marked: a ring in its colour (14px, a 2.2px stroke), a dashed ring when it has no colour, or a library icon (16px) in its
// colour (neutral when it has none).
Item {
    id: mk
    property string color: ""
    property string iconPath: ""
    property real ring: 14
    property real iconSize: 16
    implicitWidth: iconPath !== "" ? iconSize : ring
    implicitHeight: implicitWidth
    width: implicitWidth; height: implicitHeight
    Rectangle {
        visible: mk.iconPath === "" && mk.color !== ""
        anchors.centerIn: parent; width: mk.ring; height: mk.ring; radius: mk.ring / 2; color: "transparent"; border.width: 2.2; border.color: mk.color
    }
    Shape {
        visible: mk.iconPath === "" && mk.color === ""
        anchors.centerIn: parent; width: mk.ring; height: mk.ring; preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: Qt.alpha(theme.fg3, 0.75); strokeWidth: 1.4; fillColor: "transparent"; strokeStyle: ShapePath.DashLine; dashPattern: [2, 2]
            PathSvg { path: Icons.circle(mk.ring / 2, mk.ring / 2, mk.ring / 2 - 0.7) }
        }
    }
    Icon { visible: mk.iconPath !== ""; anchors.centerIn: parent; size: mk.iconSize; strokeWidth: 1.8; path: mk.iconPath; color: mk.color !== "" ? mk.color : theme.fg2 }
}
