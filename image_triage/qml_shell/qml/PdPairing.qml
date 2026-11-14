import QtQuick

// Someone asked to receive files from this PC. The six digits must match the other PC's screen, or somebody is in between.
Rectangle {
    id: p
    property var pair: ({})
    visible: !!pair.pending
    color: Qt.alpha(theme.base, 0.82)
    MouseArea { anchors.fill: parent; hoverEnabled: true; onClicked: {} }   // the card below is the only thing to answer
    Rectangle {
        anchors.centerIn: parent; width: Math.min(parent.width - 32, 340); height: body.height + 36; radius: 18; color: theme.s1; border.color: theme.line
        Column {
            id: body; x: 18; y: 18; width: parent.width - 36; spacing: 10
            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: (p.pair.name || "A PC") + " wants to receive"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; wrapMode: Text.WordWrap }
            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; width: codeText.implicitWidth + 36; height: 52; radius: 12; color: Qt.alpha(theme.accent, 0.14)
                Text { id: codeText; anchors.centerIn: parent; text: p.pair.code || ""; color: theme.accentFg; font.pixelSize: 26; font.weight: Font.DemiBold; font.letterSpacing: 3
                    font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace" } }
            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12; lineHeight: 1.25
                text: "Allow only if the other PC shows the same six digits. If they differ, someone else is in the way." }
            Row { anchors.horizontalCenter: parent.horizontalCenter; spacing: 10
                PdButton { objectName: "declineButton"; label: "Decline"; width: 120; onClicked: pocketDrop.command("pc_decline") }
                PdButton { objectName: "allowButton"; label: "Allow"; primary: true; width: 120; onClicked: pocketDrop.command("pc_allow") } }
        }
    }
}
