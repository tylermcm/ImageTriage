import QtQuick

// Minus, value, plus in one 32px box.
Rectangle {
    id: st
    property string text
    signal decrement()
    signal increment()
    width: 30 + 64 + 30 + 2; height: 32; radius: 10; color: theme.base
    border.color: theme.lineSoft; border.width: 1
    clip: true
    Repeater {
        model: 2
        delegate: Item {
            required property int index
            x: index === 0 ? 1 : st.width - 31; y: 1; width: 30; height: st.height - 2
            Rectangle { anchors.fill: parent; color: Qt.alpha(theme.lift, h.hovered ? 0.08 : 0) }
            Icon { anchors.centerIn: parent; size: 14; color: h.hovered ? theme.fg : theme.fg2; path: index === 0 ? "M6 12h12" : "M12 6v12M6 12h12" }
            HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: index === 0 ? st.decrement() : st.increment() }
            Accessible.role: Accessible.Button; Accessible.name: index === 0 ? "Decrease" : "Increase"
        }
    }
    Text { anchors.centerIn: parent; text: st.text; color: theme.fg; font.pixelSize: 13 }
}
