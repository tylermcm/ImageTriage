import QtQuick
import QtQuick.Shapes

// A dropdown: a 32px field with a chevron, and a frosted menu below it with the chosen entry ticked.
Item {
    id: sel
    property var options: []          // [{label, value}]
    property var current              // the chosen value
    property real menuMax: 10000       // cap on the menu's height; longer lists scroll
    signal picked(var value)
    readonly property string currentLabel: {
        for (var i = 0; i < options.length; ++i) if (options[i].value === current) return options[i].label
        return current === undefined || current === null ? "" : String(current)
    }
    readonly property bool opened: menu.opened
    implicitWidth: Math.max(208, lab.implicitWidth + 12 + 12 + 14 + 10)
    implicitHeight: 32
    width: implicitWidth; height: 32

    Rectangle {
        anchors.fill: parent; radius: 10
        color: hover.hovered && !sel.opened ? theme.s2 : theme.base
        border.width: 1; border.color: sel.opened ? theme.accent : (hover.hovered ? theme.line : theme.lineSoft)
        Behavior on color { ColorAnimation { duration: 200 } }
        Behavior on border.color { ColorAnimation { duration: 200 } }
    }
    Text { id: lab; x: 12; anchors.verticalCenter: parent.verticalCenter; text: sel.currentLabel; color: theme.fg; font.pixelSize: 13 }
    Icon {
        x: sel.width - 24; anchors.verticalCenter: parent.verticalCenter; size: 14; color: theme.fg3; path: "M6 9.5l6 6 6-6"
        rotation: sel.opened ? 180 : 0
        Behavior on rotation { SpringAnimation { spring: 6; damping: 0.45; epsilon: 0.5 } }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: menu.opened ? menu.close() : menu.open() }

    Popover {
        id: menu; placement: 2; minWidth: sel.width - 14; maxHeight: sel.menuMax - 14
        Repeater {
            model: sel.options
            delegate: Item {
                required property var modelData
                readonly property bool on: modelData.value === sel.current
                width: parent.width; height: 32
                implicitWidth: row.implicitWidth + 20
                Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, h.hovered ? 0.08 : 0) }
                Row { id: row; x: 10; anchors.verticalCenter: parent.verticalCenter
                    Text { text: modelData.label; font.pixelSize: 13; color: on || h.hovered ? theme.fg : theme.fg2 } }
                Icon { x: parent.width - 24; anchors.verticalCenter: parent.verticalCenter; size: 14; color: theme.accentFg; path: "M4.5 12.5l4.5 4.5 10.5-10.5"; opacity: on ? 1 : 0 }
                HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: { sel.picked(modelData.value); menu.close() } }
                Accessible.role: Accessible.MenuItem; Accessible.name: modelData.label
            }
        }
    }
    Accessible.role: Accessible.ComboBox; Accessible.name: sel.currentLabel
}
