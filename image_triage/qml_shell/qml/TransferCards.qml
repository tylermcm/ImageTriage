import QtQuick
import QtQuick.Effects

// The cards of Move / Copy To Folder and of a handoff run: confirm, progress, done. One card at a time over the dimmed, blurred application
// (the mockup's .tcard). `transfers` (transfer_model.py) holds the state; nothing is transferred yet (docs/qml_unwired.md).
FocusScope {
    id: root
    property Item sourceItem
    readonly property var c: transfers.card
    readonly property string st: c.state || ""
    anchors.fill: parent
    visible: opacity > 0.01
    opacity: transfers.active ? 1 : 0
    z: 125
    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    Keys.onEscapePressed: { if (st === "confirm" || st === "done") transfers.dismiss() }
    onStChanged: if (st !== "") forceActiveFocus()

    ShaderEffectSource { id: snap; anchors.fill: parent; sourceItem: root.sourceItem; live: true; visible: false }
    MultiEffect { anchors.fill: parent; source: snap; blurEnabled: true; blur: 1.0; blurMax: 20 }
    Rectangle { anchors.fill: parent; color: theme.light ? Qt.rgba(20 / 255, 22 / 255, 28 / 255, 0.34) : Qt.rgba(6 / 255, 7 / 255, 9 / 255, 0.52) }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onWheel: (w) => { w.accepted = true } }

    // buttons: ghost (text), normal, primary (accent) and danger (red)
    component TBtn: Item {
        id: tb
        property string text
        property string variant: "ghost"
        signal clicked()
        implicitWidth: tt.implicitWidth + 32; width: implicitWidth; height: 34
        scale: tap.pressed ? 0.96 : 1
        Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }
        Rectangle {
            anchors.fill: parent; radius: 12
            color: tb.variant === "primary" ? (th.hovered ? Qt.tint(theme.accent, Qt.alpha(theme.lift, 0.10)) : theme.accent)
                 : tb.variant === "danger" ? (th.hovered ? Qt.tint(theme.rej, Qt.alpha("white", 0.10)) : theme.rej)
                 : Qt.alpha(theme.lift, th.hovered ? 0.08 : 0)
        }
        Text { id: tt; anchors.centerIn: parent; text: tb.text; font.pixelSize: 13; font.weight: tb.variant === "ghost" ? Font.Normal : Font.Medium
               color: tb.variant === "primary" ? theme.onAccent : tb.variant === "danger" ? "#ffffff" : (th.hovered ? theme.fg : theme.fg2) }
        HoverHandler { id: th; cursorShape: Qt.PointingHandCursor }
        TapHandler { id: tap; onTapped: tb.clicked() }
        Accessible.role: Accessible.Button; Accessible.name: tb.text
    }
    component Path: Rectangle {
        property string text
        width: parent.width; height: pt.height + 20; radius: 10; color: theme.base; border.width: 1; border.color: theme.lineSoft
        Text { id: pt; x: 12; y: 10; width: parent.width - 24; text: parent.text; wrapMode: Text.WrapAnywhere; color: theme.fg; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight }
    }
    component Card: Item {
        id: cd
        property string kind                 // which state shows this card
        default property alias body: inner.data
        readonly property bool on: root.st === kind
        x: (root.width - width) / 2; y: root.height / 2 - 120
        width: Math.min(460, root.width - 48); height: inner.height + 44
        visible: opacity > 0.01; opacity: on ? 1 : 0
        scale: on ? 1 : 0.96
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }
        Shadow { anchors.fill: parent; radius: 16; offsetA: 48; insetA: 24; blurA: 64; multA: 3; alphaA: 0.85; offsetB: 12; insetB: 12; blurB: 24; alphaB: 0.5 }
        Rectangle { anchors.fill: parent; radius: 16; color: Qt.alpha(theme.s2, 0.97) }
        Rectangle { anchors.fill: parent; radius: 16; color: "transparent"; border.width: 1; border.color: Qt.alpha(theme.lift, 0.11) }
        Column { id: inner; x: 22; y: 22; width: parent.width - 44; spacing: 0 }
    }
    component H2: Text { width: parent.width; wrapMode: Text.WordWrap; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15; lineHeight: 20.25; lineHeightMode: Text.FixedHeight }
    component P: Text { width: parent.width; topPadding: 8; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 13; lineHeight: 19.5; lineHeightMode: Text.FixedHeight }

    // ---- confirm ------------------------------------------------------------------------------------------------------------
    Card {
        kind: "confirm"
        objectName: "transferConfirm"
        H2 { text: root.c.title || "" }
        P { text: root.c.body || "" }
        Item { visible: !!root.c.hasPath; width: 1; height: 12 }
        Path { visible: !!root.c.hasPath; text: root.c.path || "" }
        Item { visible: !!root.c.hasCompanions; width: parent.width; height: 14 + 22
            Text { y: 14; height: 22; verticalAlignment: Text.AlignVCenter; width: parent.width - 38 - 12; wrapMode: Text.WordWrap; text: "Include paired RAW/JPEG and sidecar files"; color: theme.fg2; font.pixelSize: 13 }
            SSwitch { x: parent.width - width; y: 14; checked: !!root.c.companions; onToggled: (v) => transfers.setCompanions(v) } }
        Item { width: 1; height: 20 }
        Row { anchors.right: parent.right; spacing: 8
            TBtn { text: "Cancel"; onClicked: transfers.dismiss() }
            TBtn { text: root.c.yes || ""; variant: root.c.danger ? "danger" : "primary"; onClicked: transfers.confirm() } }
    }

    // ---- progress -----------------------------------------------------------------------------------------------------------
    Card {
        kind: "progress"
        objectName: "transferProgress"
        H2 { text: root.c.headline || "" }
        Text { width: parent.width; topPadding: 4; elide: Text.ElideMiddle; text: root.c.route || ""; color: theme.fg3; font.pixelSize: 12; height: 20; verticalAlignment: Text.AlignVCenter }
        Item { width: 1; height: 16 - 4 }
        Rectangle { width: parent.width; height: 6; radius: 3; color: theme.s3
            Rectangle { width: parent.width * (root.c.pct || 0) / 100; height: 6; radius: 3; color: theme.accent } }
        Item { width: 1; height: 10 }
        Item { width: parent.width; height: 17
            Text { text: root.c.pctText || ""; color: theme.fg2; font.pixelSize: 12 }
            Text { anchors.right: parent.right; text: root.c.speed || ""; color: theme.fg3; font.pixelSize: 12 } }
        Item { width: 1; height: 12 }
        Column { width: parent.width; spacing: 3
            Text { width: parent.width; elide: Text.ElideMiddle; textFormat: Text.StyledText; color: theme.fg3; font.pixelSize: 12; text: "<font color='" + theme.fg2 + "'>Name:</font> " + (root.c.name || "") }
            Text { textFormat: Text.StyledText; color: theme.fg3; font.pixelSize: 12; text: "<font color='" + theme.fg2 + "'>Time remaining:</font> " + (root.c.eta || "") }
            Text { textFormat: Text.StyledText; color: theme.fg3; font.pixelSize: 12; text: "<font color='" + theme.fg2 + "'>Items remaining:</font> " + (root.c.left || "") } }
        Item { width: 1; height: 20 }
        Row { anchors.right: parent.right; spacing: 8
            TBtn { visible: root.c.canCancel !== false; text: root.c.cancelLabel || "Cancel"; onClicked: transfers.cancel() } }
    }

    // ---- done ---------------------------------------------------------------------------------------------------------------
    Card {
        kind: "done"
        objectName: "transferDone"
        Rectangle { width: 40; height: 40; radius: 20; color: Qt.alpha(theme.win, 0.16)
            Icon { anchors.centerIn: parent; size: 20; strokeWidth: 1.8; color: theme.win; path: "M4.5 12.5l4.5 4.5 10.5-10.5" } }
        Item { width: 1; height: 12 }
        H2 { text: root.c.doneTitle || "" }
        P { text: root.c.doneBody || "" }
        Item { width: 1; height: 12 }
        Path { text: root.c.path || "" }
        Item { width: 1; height: 20 }
        Row { anchors.right: parent.right; spacing: 8
            TBtn { text: "Open destination"; onClicked: transfers.openDestination() }
            TBtn { text: "OK"; variant: "primary"; onClicked: transfers.dismiss() } }
    }
}
