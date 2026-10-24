import QtQuick
import QtQuick.Controls

// A small modal card: a title, a paragraph, an optional "don't ask again" switch, and one or two buttons. Looks like the mockup's
// transfer cards (.tcard): 460px, 16px radius, frosted. Used for PocketDrop's Anywhere warning and its About text.
Popup {
    id: card
    property string title
    property string body
    property string confirmText: "OK"
    property string cancelText: ""
    property string checkText: ""
    property bool checked: false
    property bool warn: false
    signal accepted(bool checked)
    signal rejected()
    property bool answered: false

    modal: true; dim: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(460, (parent ? parent.width : 460) - 48)
    padding: 22
    closePolicy: Popup.CloseOnEscape
    onAboutToShow: { answered = false; checked = false }
    onClosed: { if (!answered) rejected() }
    Overlay.modal: Rectangle { color: Qt.rgba(6 / 255, 7 / 255, 9 / 255, 0.52) }

    enter: Transition { ParallelAnimation {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 200 }
        NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } } }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 120 } }
    background: Item {
        Shadow { anchors.fill: parent; radius: 16; offsetA: 24; insetA: 16; blurA: 48; alphaA: 0.7 }
        Rectangle { anchors.fill: parent; radius: 16; color: Qt.alpha(theme.s2, 0.97); border.color: Qt.alpha(theme.lift, 0.11) }
    }
    contentItem: Column {
        spacing: 0
        Text { width: parent.width; wrapMode: Text.WordWrap; text: card.title; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; lineHeight: 20.25; lineHeightMode: Text.FixedHeight }
        Text { width: parent.width; topPadding: 8; wrapMode: Text.WordWrap; text: card.body; color: theme.fg2; font.pixelSize: 13; lineHeight: 19.5; lineHeightMode: Text.FixedHeight }
        Row {
            visible: card.checkText !== ""; width: parent.width; topPadding: 14; spacing: 12
            Text { width: parent.width - 30 - 12; anchors.verticalCenter: parent.verticalCenter; text: card.checkText; color: theme.fg2; font.pixelSize: 13; wrapMode: Text.WordWrap }
            SSwitch { checked: card.checked; onToggled: (v) => card.checked = v; anchors.verticalCenter: parent.verticalCenter }
        }
        Item { width: 1; height: 20 }
        Row {
            anchors.right: parent.right; spacing: 8
            SButton { visible: card.cancelText !== ""; text: card.cancelText; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: { card.answered = true; card.close(); card.rejected() } }
            SButton { text: card.confirmText; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: { card.answered = true; card.close(); card.accepted(card.checked) } }
        }
    }
}
