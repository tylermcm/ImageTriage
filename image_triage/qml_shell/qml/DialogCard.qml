import QtQuick

// The frosted card of a small dialog: the mockup's .cdlg (16px radius, a 1px ring, a deep shadow) that springs in from 96%.
Item {
    id: card
    property bool shown: false
    property real radius: 16
    property bool flat: false          // standing in for a window that is already there: no spring-in and no shadow of its own
    default property alias content: inner.data
    scale: shown || flat ? 1 : 0.96
    Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }
    Shadow { visible: !card.flat; anchors.fill: parent; radius: card.radius; offsetA: 48; insetA: 24; blurA: 64; multA: 3; alphaA: 0.85; offsetB: 12; insetB: 12; blurB: 24; alphaB: 0.5 }
    Rectangle { anchors.fill: parent; radius: card.radius; color: Qt.alpha(theme.s2, 0.96) }
    Rectangle { anchors.fill: parent; anchors.margins: -1; radius: card.radius + 1; color: "transparent"; border.color: theme.lineSoft }
    Rectangle { anchors.fill: parent; radius: card.radius; color: "transparent"; border.color: Qt.alpha(theme.lift, 0.11) }
    Item { id: inner; anchors.fill: parent }
    MouseArea { anchors.fill: parent; z: -1; onWheel: (w) => { w.accepted = true } }
}
