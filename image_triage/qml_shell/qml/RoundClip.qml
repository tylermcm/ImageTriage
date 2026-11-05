import QtQuick
import QtQuick.Effects

// Clips its children to a rounded rectangle (a plain `clip` only cuts to the rectangle).
Item {
    id: rc
    property real radius: 10
    default property alias content: holder.data
    // Qt's software scene graph cannot render MultiEffect. Keep the content
    // visible with rectangular clipping when shaders are unavailable.
    readonly property bool softwareRendering: GraphicsInfo.api === GraphicsInfo.Software
    clip: softwareRendering
    layer.enabled: !softwareRendering
    layer.effect: MultiEffect { maskEnabled: true; maskSource: mask; maskThresholdMin: 0.5; maskSpreadAtMin: 1.0 }
    Item { id: holder; anchors.fill: parent }
    Item { id: mask; anchors.fill: parent; visible: false; layer.enabled: true
        Rectangle { anchors.fill: parent; radius: rc.radius } }
}
