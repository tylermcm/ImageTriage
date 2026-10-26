import QtQuick
import QtQuick.Shapes

// A 40 px icon button for the rail's pinned area: tooltip on hover, optional menu caret, highlight when on.
Item {
    id: b
    property string icon
    property string tip
    property bool menu: false
    property bool on: false
    property color hoverColor: theme.fg
    default property alias extra: holder.data
    signal clicked()
    property real size: UiState.railPin          // the rail shrinks its pinned buttons in a short window
    readonly property real sz: size
    width: sz; height: sz
    scale: press.pressed ? 0.92 : 1
    Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }

    Rectangle {
        anchors.fill: parent; radius: 12
        color: b.on ? Qt.alpha(theme.accent, 0.16) : Qt.alpha(theme.lift, hover.hovered ? 0.08 : 0)
        Behavior on color { ColorAnimation { duration: 140 } }
    }
    Icon { anchors.centerIn: parent; name: b.icon; size: 20; color: b.on ? theme.accentFg : (hover.hovered ? b.hoverColor : theme.fg) }   // idle pins use the full text colour in the mockup
    Shape {
        visible: b.menu; x: 29; y: 29; width: 6; height: 6; preferredRendererType: Shape.CurveRenderer
        ShapePath { strokeColor: "transparent"; fillColor: Qt.alpha(theme.fg3, 0.9); PathSvg { path: "M6 0L6 6L0 6z" } }
    }
    Tip { id: tip; text: b.tip; show: false }
    Timer { id: delay; interval: 350; onTriggered: tip.show = hover.hovered && !b.on }
    HoverHandler {
        id: hover; cursorShape: Qt.PointingHandCursor
        onHoveredChanged: { if (hovered) delay.restart(); else { delay.stop(); tip.show = false } }
    }
    TapHandler { id: press; onTapped: { tip.show = false; b.clicked() } }
    Item { id: holder; anchors.fill: parent }
    Accessible.role: Accessible.Button
    Accessible.name: b.tip
}
