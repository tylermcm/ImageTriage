import QtQuick
import QtQuick.Shapes

// A three-channel histogram drawn from path data (a stand-in for the real per-photo histogram).
Item {
    id: h
    property color c1: three ? Qt.rgba(160 / 255, 170 / 255, 190 / 255, 0.4) : Qt.rgba(1, 1, 1, 0.35)
    property color c2: Qt.rgba(90 / 255, 150 / 255, 1.0, three ? 0.55 : 0.5)
    property color c3: Qt.rgba(80 / 255, 200 / 255, 140 / 255, 0.45)
    property bool three: false
    Shape {
        width: 100; height: 40; preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: h.width / 100; yScale: h.height / 40 }
        ShapePath { strokeColor: "transparent"; fillColor: h.c1; PathSvg { path: "M0 40L0 34C8 30 14 22 22 18C30 14 36 22 44 16C52 10 58 6 66 12C74 18 82 26 100 34L100 40z" } }
        ShapePath { strokeColor: "transparent"; fillColor: h.c2; PathSvg { path: "M0 40L0 36C10 34 18 26 28 22C38 18 46 14 56 18C66 22 78 30 100 36L100 40z" } }
        ShapePath { strokeColor: "transparent"; fillColor: h.three ? h.c3 : "transparent"; PathSvg { path: "M0 40L0 38C14 36 24 30 34 26C46 22 56 24 68 28C80 32 90 36 100 38L100 40z" } }
    }
}
