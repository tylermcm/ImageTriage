import QtQuick
import QtQuick.Shapes

// The Image Triage logo, drawn from its own geometry (a 1024 box: an ember ring of radius 344, six round blades turned 60 degrees apart about the
// centre, a white dot), with no tile behind it. `spinning` turns the whole mark at a steady pace; it is the working indicator of the Cull window.
Item {
    id: mark
    property real size: 24
    property bool spinning: false
    property int period: 1400                      // ms per turn
    width: size; height: size

    Item {
        id: art
        width: 1024; height: 1024
        scale: mark.size / 1024
        transformOrigin: Item.TopLeft
        Item {
            id: turner
            anchors.fill: parent
            transformOrigin: Item.Center
            RotationAnimator on rotation { running: mark.spinning && mark.visible; loops: Animation.Infinite; from: 0; to: 360; duration: mark.period }
            Shape {
                anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: "transparent"; fillRule: ShapePath.OddEvenFill
                    fillGradient: LinearGradient {
                        x1: 170; y1: 860; x2: 860; y2: 170
                        GradientStop { position: 0; color: "#ffbb4d" }
                        GradientStop { position: 0.55; color: "#ff8a32" }
                        GradientStop { position: 1; color: "#ff6a2a" }
                    }
                    PathAngleArc { centerX: 512; centerY: 512; radiusX: 374; radiusY: 374; startAngle: 0; sweepAngle: 360 }
                    PathMove { x: 826; y: 512 }
                    PathAngleArc { centerX: 512; centerY: 512; radiusX: 314; radiusY: 314; startAngle: 0; sweepAngle: 360 }
                }
            }
            Repeater {
                model: 6
                delegate: Item {
                    required property int index
                    x: 512; y: 512; width: 0; height: 0
                    rotation: index * 60
                    transformOrigin: Item.TopLeft
                    // the blade runs from (170, 510) to (462, 392) before it is turned: a 60 wide capsule, lighter at its outer end
                    Rectangle {
                        x: 170 - 512 - 30; y: 510 - 512 - 30; width: 315 + 60; height: 60; radius: 30; antialiasing: true
                        transform: Rotation { origin.x: 30; origin.y: 30; angle: -22.0 }
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: "#ffc24d" }
                            GradientStop { position: 1; color: "#ff6a2a" }
                        }
                    }
                }
            }
            Rectangle { x: 512 - 36; y: 512 - 36; width: 72; height: 72; radius: 36; color: "white"; antialiasing: true }
        }
    }
}
