import QtQuick

// What follows the pointer while a collection is dragged towards PocketDrop (the mockup's .pdx-ghost): stacked thumbnails, name, count.
Item {
    id: ghost
    property var info: ({ title: "", sub: "", thumbs: ["", "", ""] })
    width: row.width + 22; height: 42
    rotation: -2
    Shadow { anchors.fill: parent; radius: 12; offsetA: 14; insetA: 10; blurA: 34; alphaA: 0.65 }
    Rectangle { anchors.fill: parent; radius: 12; color: Qt.alpha(theme.s3, 0.96); border.color: Qt.alpha(theme.lift, 0.14) }
    Row {
        id: row; x: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 10
        Item { width: 54; height: 30; anchors.verticalCenter: parent.verticalCenter
            Repeater { model: 3
                delegate: Item { required property int index; x: index * 12; width: 30; height: 30
                    Rectangle { anchors.fill: parent; radius: 8; color: theme.s3 }
                    RoundClip { x: 2; y: 2; width: 26; height: 26; radius: 6
                        Rectangle { anchors.fill: parent; color: theme.s2 }
                        ThumbImage { anchors.fill: parent; bucket: 160; path: ghost.info.thumbs && ghost.info.thumbs[index] ? ghost.info.thumbs[index] : "" } } } } }
        Column { anchors.verticalCenter: parent.verticalCenter
            Text { text: ghost.info.title; color: theme.fg; font.pointSize: UiState.pt(12.5); height: 17; verticalAlignment: Text.AlignVCenter }
            Text { text: ghost.info.sub; color: theme.fg3; font.pixelSize: 11; height: 15; verticalAlignment: Text.AlignVCenter } }
    }
}
