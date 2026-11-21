import QtQuick

// Minus, value, plus in one 32px box. The value can be typed too: it is handed over (`edited`) on Enter or when the box loses focus.
Rectangle {
    id: st
    property string text
    property bool editable: false        // typing needs an onEdited that takes the value
    signal decrement()
    signal increment()
    signal edited(string text)
    width: 30 + 64 + 30 + 2; height: 32; radius: 10; color: theme.base
    border.color: input.activeFocus ? theme.accent : theme.lineSoft; border.width: 1
    clip: true
    Repeater {
        model: 2
        delegate: Item {
            required property int index
            x: index === 0 ? 1 : st.width - 31; y: 1; width: 30; height: st.height - 2
            Rectangle { anchors.fill: parent; color: Qt.alpha(theme.lift, h.hovered ? 0.08 : 0) }
            Icon { anchors.centerIn: parent; size: 14; color: h.hovered ? theme.fg : theme.fg2; path: index === 0 ? "M6 12h12" : "M12 6v12M6 12h12" }
            HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: { input.focus = false; index === 0 ? st.decrement() : st.increment() } }
            Accessible.role: Accessible.Button; Accessible.name: index === 0 ? "Decrease" : "Increase"
        }
    }
    TextInput {
        id: input
        objectName: "stepperInput"
        x: 31; width: st.width - 62; height: st.height; verticalAlignment: TextInput.AlignVCenter; horizontalAlignment: TextInput.AlignHCenter
        readOnly: !st.editable; activeFocusOnPress: st.editable; selectByMouse: st.editable; color: theme.fg; selectionColor: Qt.alpha(theme.accent, 0.45); font.pixelSize: 13
        validator: RegularExpressionValidator { regularExpression: /-?\d{0,7}/ }
        text: st.text
        function hand() { if (text !== st.text) st.edited(text); text = Qt.binding(() => st.text) }
        onAccepted: { hand(); focus = false }
        onActiveFocusChanged: { if (activeFocus) selectAll(); else hand() }
        Keys.onUpPressed: st.increment()
        Keys.onDownPressed: st.decrement()
        Keys.onEscapePressed: (e) => { text = Qt.binding(() => st.text); focus = false; e.accepted = true }
        HoverHandler { cursorShape: Qt.IBeamCursor }
    }
}
