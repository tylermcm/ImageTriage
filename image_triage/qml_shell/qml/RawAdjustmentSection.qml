import QtQuick

Column {
        id: section
        property color ink: theme.fg
        property color subtle: theme.fg3
        property color line: theme.line
        property string title: ""
        property bool expanded: false
        property int inset: 13
        default property alias contents: body.data
        width: parent.width
        Item {
            width: parent.width; height: 36.84
            Text {
                x: section.inset; anchors.verticalCenter: parent.verticalCenter
                renderType: Text.NativeRendering; text: "\u203a"; color: section.subtle; font.pixelSize: 16
                rotation: section.expanded ? 90 : 0
            }
            Text { color: section.ink; font.pixelSize: 13; renderType: Text.NativeRendering; x: section.inset + 13; anchors.verticalCenter: parent.verticalCenter; text: section.title; font.weight: Font.Bold }
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
