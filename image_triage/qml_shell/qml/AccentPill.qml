import QtQuick
import QtQuick.Effects

// The selected-destination highlight shared by the rail and the settings list: the accent at 15% with a 1px accent ring at 30%
// laid over it, and the soft glow underneath (box-shadow 0 6px 18px -8px accent, drawn only outside the pill).
Item {
    id: pill
    property real radius: 14
    property real glowBlur: 32          // matched against the browser's 18px box-shadow blur
    property real glowInset: 6
    Item {
        x: -40; y: -40; width: pill.width + 80; height: pill.height + 80
        layer.enabled: true
        layer.effect: MultiEffect { maskEnabled: true; maskInverted: true; maskSource: pillMask; maskThresholdMin: 0.5; maskSpreadAtMin: 1.0 }
        Rectangle {
            x: 40 + pill.glowInset; y: 40 + pill.glowInset + 6; width: pill.width - 2 * pill.glowInset; height: pill.height - 2 * pill.glowInset; radius: 8; color: theme.accent
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: pill.glowBlur; autoPaddingEnabled: true }
        }
    }
    Item { id: pillMask; x: -40; y: -40; width: pill.width + 80; height: pill.height + 80; visible: false; layer.enabled: true
        Rectangle { x: 40; y: 40; width: pill.width; height: pill.height; radius: pill.radius } }
    Rectangle { anchors.fill: parent; radius: pill.radius; color: Qt.alpha(theme.accent, 0.15) }
    Rectangle { anchors.fill: parent; radius: pill.radius; color: "transparent"; border.color: Qt.alpha(theme.accent, 0.30); border.width: 1 }
}
