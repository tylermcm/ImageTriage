import QtQuick
import QtQuick.Controls

// A small card that asks for one name (New folder, Rename): the field, what is wrong with the name as it is typed, and OK.
// `check(name)` says what is wrong ("" when nothing is); `accepted(name)` is the name, and the card closes when the caller says so.
Popup {
    id: card
    objectName: "namePromptCard"
    property string title
    property string confirmText: "OK"
    property string text: ""
    property var check: function(name) { return "" }
    property string problem: ""
    signal accepted(string name)

    function ask(title, current, confirm) {
        card.title = title; card.confirmText = confirm; card.text = current; card.problem = ""
        open()
        field.input.forceActiveFocus(); field.input.selectAll()
    }
    function submit() {
        problem = check(text)
        if (problem === "") accepted(text.trim())
    }

    parent: Overlay.overlay
    modal: true; dim: true; focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(420, (parent ? parent.width : 420) - 48)
    padding: 22
    closePolicy: Popup.CloseOnEscape
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
        Text { width: parent.width; wrapMode: Text.WordWrap; text: card.title; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold }
        Item { width: 1; height: 14 }
        FInput {
            id: field; objectName: "namePromptField"; width: parent.width; text: card.text
            onEdited: (v) => { card.text = v; card.problem = card.check(v) }
        }
        Connections { target: field.input; function onAccepted() { card.submit() } }       // Return
        Text { width: parent.width; topPadding: 6; visible: card.problem !== ""; wrapMode: Text.WordWrap; text: card.problem; color: theme.rej; font.pixelSize: 12 }
        Item { width: 1; height: 18 }
        Row {
            anchors.right: parent.right; spacing: 8
            SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: card.close() }
            SButton { objectName: "namePromptOk"; text: card.confirmText; variant: "primary"; h: 34; hpad: 16; radius: 12
                      enabled: card.text.trim() !== "" && card.problem === ""; onClicked: card.submit() }
        }
    }
}
