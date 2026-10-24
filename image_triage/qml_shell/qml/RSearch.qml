import QtQuick

// The search box of the help and reference windows (the mockup's .r-sf): a 36px field with a magnifier.
Rectangle {
    id: f
    property string text
    property string placeholder
    property alias input: ti
    signal edited(string value)
    height: 36; radius: 11; color: theme.base
    border.width: 1; border.color: ti.activeFocus ? theme.accent : theme.lineSoft
    Behavior on border.color { ColorAnimation { duration: 200 } }
    Icon { x: 12; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 16; strokeWidth: 1.7; color: theme.fg3 }
    TextInput {
        id: ti
        x: 37; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 37 - 12
        color: theme.fg; font.pixelSize: 13; clip: true; selectByMouse: true
        text: f.text
        onTextEdited: f.edited(text)
        Text { visible: !parent.text; text: f.placeholder; color: theme.fg3; font: parent.font; width: parent.width; elide: Text.ElideRight }
        Accessible.name: f.placeholder
    }
}
