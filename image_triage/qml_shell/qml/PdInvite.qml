import QtQuick

// Another PC wants to send files here. Nothing is fetched until this is accepted.
Rectangle {
    id: p
    property var invite: null
    visible: !!invite
    color: Qt.alpha(theme.base, 0.82)
    MouseArea { anchors.fill: parent; hoverEnabled: true; onClicked: {} }
    Rectangle {
        anchors.centerIn: parent; width: Math.min(parent.width - 32, 340); height: body.height + 36; radius: 18; color: theme.s1; border.color: theme.line
        Column {
            id: body; x: 18; y: 18; width: parent.width - 36; spacing: 10
            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; width: 48; height: 48; radius: 24; color: Qt.alpha(theme.accent, 0.16)
                PdIcon { anchors.centerIn: parent; size: 24; strokeWidth: 1.8; color: theme.accentFg; path: "M4 5h16v10H4zM9 19h6M12 15v4" } }
            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold
                text: ((p.invite && p.invite.name) || "A PC") + " wants to send you files" }
            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12; lineHeight: 1.25
                text: "You will be asked to compare a code with theirs before anything arrives." }
            Row { anchors.horizontalCenter: parent.horizontalCenter; spacing: 10
                PdButton { objectName: "declineInvite"; label: "Decline"; width: 120; onClicked: pocketDrop.command("pc_invite_decline") }
                PdButton { objectName: "acceptInvite"; label: "Accept"; primary: true; width: 120; onClicked: pocketDrop.command("pc_invite_accept") } }
        }
    }
}
