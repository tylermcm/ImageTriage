import QtQuick

// A 34px text field: the filters window's box with a placeholder, an optional unit and an inline error.
Rectangle {
    id: f
    property string text
    property string placeholder
    property string suffix
    property string error                 // shown in red at the right instead of the unit
    property alias input: ti
    signal edited(string value)
    height: 34; radius: 10; color: theme.base
    border.width: 1; border.color: ti.activeFocus ? theme.accent : (hover.hovered ? theme.line : theme.lineSoft)
    Behavior on border.color { ColorAnimation { duration: 200 } }
    HoverHandler { id: hover }
    TextInput {
        id: ti
        x: 10; anchors.verticalCenter: parent.verticalCenter
        width: parent.width - 20 - (tail.visible ? tail.implicitWidth + 8 : 0)
        color: theme.fg; font.pixelSize: 13; clip: true; selectByMouse: true
        text: f.text
        onTextEdited: f.edited(text)
        onTextChanged: { if (!activeFocus) cursorPosition = 0 }      // a long value shows its start, not its end
        onActiveFocusChanged: { if (!activeFocus) cursorPosition = 0 }
        Text { visible: !parent.text; text: f.placeholder; color: theme.fg3; font: parent.font; width: parent.width; elide: Text.ElideRight }
        Accessible.name: f.placeholder
    }
    Text {
        id: tail
        visible: f.error !== "" || f.suffix !== ""
        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
        text: f.error !== "" ? f.error : f.suffix; color: f.error !== "" ? theme.rej : theme.fg3; font.pixelSize: 12
    }
}
