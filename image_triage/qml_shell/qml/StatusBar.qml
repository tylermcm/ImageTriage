import QtQuick

Rectangle {
    height: 26
    color: theme.s1
    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
    Row {
        anchors.verticalCenter: parent.verticalCenter; x: 12; spacing: 16
        Text { text: photos.shown + " photos"; color: theme.fg3; font.pointSize: UiState.pt(11.5) }
        Text { text: (photos.picked > 0 ? photos.picked : 1) + " selected"; color: theme.fg3; font.pointSize: UiState.pt(11.5); visible: !UiState.compact }
        Text { text: photos.loading ? "Reading folder…" : "Thumbnails ready"; color: theme.fg3; font.pointSize: UiState.pt(11.5) }
    }
    Text { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: "Sample data"; color: theme.fg3; font.pointSize: UiState.pt(11.5) }
}
