import QtQuick

// Short messages at the bottom right: what just happened, with an Undo or a shortcut where there is one. Up to three, each leaves by itself.
// The model is menu_model.py (`toasts`).
Item {
    id: root
    objectName: "toasts"
    z: 130
    width: 364; height: col.height
    x: parent.width - 16 - width; y: parent.height - 40 - height
    readonly property var colors: ({ success: theme.win, info: theme.accentFg, warn: theme.rev, error: theme.rej })
    readonly property var icons: ({ success: "M4.5 12.5l4.5 4.5 10.5-10.5", info: "M12 8v5M12 16.5v.01M12 21a9 9 0 100-18 9 9 0 000 18z",
                                    warn: "M12 8v5M12 16.5v.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
                                    error: "M12 8v5M12 16.5v.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" })
    Column {
        id: col
        width: parent.width; spacing: 8
        Repeater {
            model: toasts.items
            delegate: Item {
                id: tc
                required property var modelData
                readonly property color tone: root.colors[modelData.kind]
                width: col.width; height: Math.max(body.implicitHeight + 24, 56)
                scale: 1
                Component.onCompleted: { scale = 0.96; opacity = 0; enter.start() }
                ParallelAnimation { id: enter
                    NumberAnimation { target: tc; property: "opacity"; to: 1; duration: 200 }
                    NumberAnimation { target: tc; property: "scale"; to: 1; duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }
                Shadow { anchors.fill: parent; radius: 14; offsetA: 24; insetA: 16; blurA: 48; alphaA: 0.7 }
                Rectangle { anchors.fill: parent; radius: 14; color: Qt.alpha(theme.s2, 0.96); border.color: Qt.alpha(theme.lift, 0.11) }
                RoundClip { anchors.fill: parent; radius: 14
                    Rectangle { x: 0; y: parent.height - 2; height: 2; color: tc.tone; opacity: 0.7
                        width: toasts.still ? parent.width * 0.55 : parent.width
                        NumberAnimation on width { running: !toasts.still; from: tc.width; to: 0; duration: 6000 } } }
                Rectangle { x: 12; y: 12; width: 30; height: 30; radius: 10; color: Qt.tint(theme.s3, Qt.alpha(tc.tone, 0.18))
                    Icon { anchors.centerIn: parent; size: 17; strokeWidth: 2; path: root.icons[tc.modelData.kind]; color: tc.tone } }
                Column { id: body; x: 54; y: 12; width: parent.width - 54 - actions.width - 12 - 6; spacing: 2
                    Text { width: parent.width; wrapMode: Text.WordWrap; text: tc.modelData.title; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; lineHeight: 17.5; lineHeightMode: Text.FixedHeight; topPadding: 1 }
                    Text { visible: tc.modelData.body !== ""; width: parent.width; wrapMode: Text.WordWrap; text: tc.modelData.body; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight } }
                Row { id: actions; anchors.right: parent.right; anchors.rightMargin: 12; y: 13; spacing: 4
                    Item { visible: tc.modelData.action !== ""; width: al.implicitWidth + 22; height: 28
                        Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, ah.hovered ? 0.14 : 0.08) }
                        Text { id: al; anchors.centerIn: parent; text: tc.modelData.action; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                        HoverHandler { id: ah; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: toasts.trigger(tc.modelData.id) }
                        Accessible.role: Accessible.Button; Accessible.name: tc.modelData.action }
                    Item { width: 26; height: 28
                        Icon { anchors.centerIn: parent; name: "x"; size: 14; strokeWidth: 1.8; color: xh.hovered ? theme.fg : theme.fg3 }
                        HoverHandler { id: xh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: toasts.close(tc.modelData.id) }
                        Accessible.role: Accessible.Button; Accessible.name: "Dismiss" } }
            }
        }
    }
}
