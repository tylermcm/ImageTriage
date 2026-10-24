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

    // soft drop shadow
    Rectangle {
        id: shadowSrc
        x: 0; y: 14; width: glass.width; height: glass.height; radius: glass.radius
        color: "black"; opacity: 0.55
        layer.enabled: true
        layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 36; autoPaddingEnabled: true }
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
        blurEnabled: true; blur: 1.0; blurMax: glass.blurRadius; saturation: 0.5
        autoPaddingEnabled: false
        maskEnabled: true; maskSource: maskItem
        maskThresholdMin: 0.5; maskSpreadAtMin: 1.0
    }
    Rectangle { anchors.fill: parent; radius: glass.radius; color: glass.tint; border.color: Qt.alpha(theme.lift, 0.11); border.width: 1 }
}
