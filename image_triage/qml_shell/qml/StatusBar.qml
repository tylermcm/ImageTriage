import QtQuick

Rectangle {
    height: 26
    color: theme.s1
    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
    Row {
        anchors.verticalCenter: parent.verticalCenter; x: 12; spacing: 16
        Text { text: photos.shown + " photos"; color: theme.fg3; font.pixelSize: 12 }
        Text { text: "1 selected"; color: theme.fg3; font.pixelSize: 12; visible: parent.parent.width > 640 }
        Text { text: photos.loading ? "Reading folder…" : "Thumbnails ready"; color: theme.fg3; font.pixelSize: 12 }
    }
    Text { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: "Sample data"; color: theme.fg3; font.pixelSize: 12 }
}
