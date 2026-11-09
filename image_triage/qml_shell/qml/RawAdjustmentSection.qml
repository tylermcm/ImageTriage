import QtQuick

Column {
        id: section
        property color ink: theme.light ? theme.fg : "#e8ebf0"
        property color subtle: theme.light ? theme.fg3 : "#848c99"
        property color line: theme.light ? theme.line : "#2b2e33"
        property string title: ""
        property bool expanded: false
        property int inset: 13
        default property alias contents: body.data
        width: parent.width
        Item {
            width: parent.width; height: 36.84
            Text {
                x: section.inset; anchors.verticalCenter: parent.verticalCenter
                font.family: "Segoe UI"; renderType: Text.NativeRendering; text: "\u203a"; color: section.subtle; font.pixelSize: 16
                rotation: section.expanded ? 90 : 0
            }
            Text { color: section.ink; font.family: "Segoe UI"; font.pixelSize: 13; renderType: Text.NativeRendering; x: section.inset + 13; anchors.verticalCenter: parent.verticalCenter; text: section.title; font.weight: Font.Bold }
            TapHandler { onTapped: section.expanded = !section.expanded }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            Accessible.role: Accessible.Button; Accessible.name: section.title
            Accessible.onPressAction: section.expanded = !section.expanded
        }
        Column {
            id: body
            visible: section.expanded
            width: parent.width - section.inset * 2; x: section.inset
            bottomPadding: 10
        }
        Rectangle { width: parent.width; height: 1; color: section.line }
    }
