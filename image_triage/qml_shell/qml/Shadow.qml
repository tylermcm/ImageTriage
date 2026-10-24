import QtQuick
import QtQuick.Effects

// CSS box-shadow(s) around a rounded rectangle, drawn only outside it: a smaller copy lowered and blurred (negative spread = inset),
// masked with the inverted shape so nothing shows through a translucent surface.
Item {
    id: sh
    property real radius: 14
    property real offsetA: 22
    property real insetA: 14           // negative spread
    property real blurA: 64
    property real alphaA: 0.65
    property real offsetB: 3
    property real blurB: 8
    property real alphaB: 0           // second shadow (0 = none)
    readonly property real pad: 160
    Item {
        x: -sh.pad; y: -sh.pad; width: sh.width + 2 * sh.pad; height: sh.height + 2 * sh.pad
        layer.enabled: true
        layer.effect: MultiEffect { maskEnabled: true; maskInverted: true; maskSource: shapeMask; maskThresholdMin: 0.5; maskSpreadAtMin: 1.0 }
        Rectangle {
            x: sh.pad + sh.insetA; y: sh.pad + sh.insetA + sh.offsetA; width: sh.width - 2 * sh.insetA; height: sh.height - 2 * sh.insetA
            radius: Math.max(0, sh.radius - sh.insetA); color: "black"; opacity: sh.alphaA
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: sh.blurA; autoPaddingEnabled: true }
        }
        Rectangle {
            visible: sh.alphaB > 0
            x: sh.pad; y: sh.pad + sh.offsetB; width: sh.width; height: sh.height; radius: sh.radius; color: "black"; opacity: sh.alphaB
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: sh.blurB; autoPaddingEnabled: true }
        }
    }
    Item { id: shapeMask; x: -sh.pad; y: -sh.pad; width: sh.width + 2 * sh.pad; height: sh.height + 2 * sh.pad; visible: false; layer.enabled: true
        Rectangle { x: sh.pad; y: sh.pad; width: sh.width; height: sh.height; radius: sh.radius } }
}
