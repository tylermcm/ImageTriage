import QtQuick
import QtQuick.Effects

// The overlay every dialog sits on: the application blurred and dimmed behind it. A click outside the card dismisses it.
FocusScope {
    id: root
    property Item sourceItem
    property bool shown: false
    signal dismissed()
    default property alias content: holder.data
    anchors.fill: parent
    visible: opacity > 0.01
    opacity: shown ? 1 : 0
    z: 108
    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    ShaderEffectSource { id: snap; anchors.fill: parent; sourceItem: root.sourceItem; live: true; visible: false }
    MultiEffect { anchors.fill: parent; source: snap; blurEnabled: true; blur: 1.0; blurMax: 20 }
    Rectangle { anchors.fill: parent; color: theme.light ? Qt.rgba(20 / 255, 22 / 255, 28 / 255, 0.34) : Qt.rgba(6 / 255, 7 / 255, 9 / 255, 0.52) }
    MouseArea { anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.AllButtons; onClicked: root.dismissed(); onWheel: (w) => { w.accepted = true } }
    HoverHandler { blocking: true }        // nothing behind a dialog (the grid's hover strips, tooltips) reacts to the pointer while it is up
    Item { id: holder; anchors.fill: parent }
    Keys.onEscapePressed: root.dismissed()
}
