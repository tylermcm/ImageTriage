import QtQuick

Rectangle {
    height: 26
    color: theme.s1
    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
    Row {
        anchors.verticalCenter: parent.verticalCenter; x: 12; spacing: 16
        Text { text: photos.shown + " photos"; color: theme.fg3; font.pointSize: UiState.pt(11.5) }
        Text { text: photos.picked + " selected"; color: theme.fg3; font.pointSize: UiState.pt(11.5); visible: !UiState.compact && photos.picked > 0 }
        Text { visible: photos.loading; text: "Reading folder…"; color: theme.fg3; font.pointSize: UiState.pt(11.5) }
    }
}
