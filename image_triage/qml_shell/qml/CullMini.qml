import QtQuick

// The small card shown while the cull runs in the background (you chose "Keep browsing"): progress, Show and Cancel.
Item {
    id: mini
    readonly property var v: cull.view
    visible: v.mini
    z: 30
    x: 86; y: parent.height - 44 - height
    width: 300; height: col.implicitHeight + 24
    Shadow { anchors.fill: parent; radius: 14; offsetA: 24; insetA: 16; blurA: 48; alphaA: 0.7 }
    Rectangle { anchors.fill: parent; radius: 14; color: Qt.alpha(theme.s2, 0.96); border.color: Qt.alpha(theme.lift, 0.11) }
    // the card takes every click and scroll that lands on it: without this they went through to the grid under it (a folder card opened, and the cull with it)
    MouseArea { anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.AllButtons; onWheel: (w) => { w.accepted = true } }
    HoverHandler { blocking: true }
    Column { id: col; x: 14; y: 12; width: parent.width - 28; spacing: 0
        Row { spacing: 8; height: 20
            Icon { name: "spark"; size: 15; strokeWidth: 1.8; color: theme.accentFg; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Culling " + mini.v.folder; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter; width: col.width - 24; elide: Text.ElideRight } }
        Text { topPadding: 3; text: mini.v.miniLine; color: theme.fg3; font.pixelSize: 12 }
        Rectangle { y: 9; width: parent.width; height: 5; radius: 2.5; color: theme.s3
            Rectangle { width: parent.width * mini.v.runPct / 100; height: 5; radius: 2.5; gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: theme.accent } GradientStop { position: 1; color: "#d58ae8" } } } }
        Item { width: 1; height: 19 }
        Row { spacing: 6
            SButton { text: "Show"; h: 28; hpad: 11; radius: 9; onClicked: cull.openCull() }
            SButton { text: "Cancel"; h: 28; hpad: 11; radius: 9; onClicked: cull.cancelRun() } } }
}
