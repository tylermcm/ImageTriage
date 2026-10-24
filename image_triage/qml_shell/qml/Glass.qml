import QtQuick
import QtQuick.Effects

// Frosted glass: blurs whatever `sourceItem` (the grid) draws behind this item, tints it, and rounds the corners.
Item {
    id: glass
    property Item sourceItem
    property real radius: 20
    property color tint: Qt.alpha(theme.s1, 0.72)
    property int blurRadius: 64
    readonly property int pad: 40
    clip: false

    // box-shadow: 0 22px 44px -14px rgba(0,0,0,.65), 0 3px 8px rgba(0,0,0,.28). CSS draws it only outside the border box, so the
    // layer is masked with the inverted bar shape (otherwise it would show through the translucent glass).
    property real shadowBlurA: 64
    property real shadowBlurB: 8
    property real shadowAlphaA: 0.65
    property real shadowAlphaB: 0.28
    property real shadowOffsetA: 22
    property real shadowInset: 14          // negative spread
    property real saturation: 0.5
    Item {
        id: shadowLayer
        x: -100; y: -80; width: glass.width + 200; height: glass.height + 220
        layer.enabled: true
        layer.effect: MultiEffect { maskEnabled: true; maskInverted: true; maskSource: barMask; maskThresholdMin: 0.5; maskSpreadAtMin: 1.0 }
        Rectangle {
            x: 100 + glass.shadowInset; y: 80 + glass.shadowInset + glass.shadowOffsetA; width: glass.width - 2 * glass.shadowInset; height: glass.height - 2 * glass.shadowInset; radius: Math.max(0, glass.radius - glass.shadowInset)
            color: "black"; opacity: glass.shadowAlphaA
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: glass.shadowBlurA; autoPaddingEnabled: true }
        }
        Rectangle {
            x: 100; y: 80 + 3; width: glass.width; height: glass.height; radius: glass.radius
            color: "black"; opacity: glass.shadowAlphaB; visible: glass.shadowAlphaB > 0
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: glass.shadowBlurB; autoPaddingEnabled: true }
        }
    }
    Item {
        id: barMask
        x: -100; y: -80; width: glass.width + 200; height: glass.height + 220; visible: false; layer.enabled: true
        Rectangle { x: 100; y: 80; width: glass.width; height: glass.height; radius: glass.radius }
    }

    Item {
        id: maskItem
        x: -glass.pad; y: -glass.pad; width: glass.width + glass.pad * 2; height: glass.height + glass.pad * 2
        layer.enabled: true
        visible: false
        Rectangle { x: glass.pad; y: glass.pad; width: glass.width; height: glass.height; radius: glass.radius }
    }
    ShaderEffectSource {
        id: ses
        x: -glass.pad; y: -glass.pad; width: glass.width + glass.pad * 2; height: glass.height + glass.pad * 2
        sourceItem: glass.sourceItem
        live: true
        recursive: false
        sourceRect: {
            if (!glass.sourceItem) return Qt.rect(0, 0, 0, 0);
            var p = glass.mapToItem(glass.sourceItem, -glass.pad, -glass.pad);
            return Qt.rect(p.x, p.y, glass.width + glass.pad * 2, glass.height + glass.pad * 2);
        }
        visible: false
    }
    MultiEffect {
        x: -glass.pad; y: -glass.pad; width: glass.width + glass.pad * 2; height: glass.height + glass.pad * 2
        source: ses
        blurEnabled: true; blur: 1.0; blurMax: glass.blurRadius; saturation: glass.saturation
        autoPaddingEnabled: false
        maskEnabled: true; maskSource: maskItem
        maskThresholdMin: 0.5; maskSpreadAtMin: 1.0
    }
    Rectangle { anchors.fill: parent; radius: glass.radius; color: glass.tint; border.color: Qt.alpha(theme.lift, 0.11); border.width: 1 }
    // inset 0 1px 0 lift 9%: a top highlight that follows the rounded corner
    Rectangle { x: 1; y: 1; width: parent.width - 2; height: parent.height - 2; radius: glass.radius - 1; color: "transparent"; border.color: Qt.alpha(theme.lift, 0.09); border.width: 1
        layer.enabled: true
        layer.effect: MultiEffect { maskEnabled: true; maskSource: hiMask; maskInverted: false }
    }
    Item { id: hiMask; x: 1; y: 1; width: parent.width - 2; height: 12; visible: false; layer.enabled: true; Rectangle { anchors.fill: parent; color: "white" } }
}
