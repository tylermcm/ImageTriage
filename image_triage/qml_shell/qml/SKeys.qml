import QtQuick
import QtQuick.Shapes

// A shortcut row's control: the chord as key caps (or a pulsing "Press the new keys…" chip while recording) and the Change button.
Item {
    id: k
    property var keys: []
    property bool recording: false
    signal toggled()
    signal chord(string text)
    width: row.implicitWidth; height: 30
    Row {
        id: row; spacing: 10; anchors.verticalCenter: parent.verticalCenter
        Item {
            width: k.recording ? rec.width : caps.implicitWidth; height: 26; anchors.verticalCenter: parent.verticalCenter
            Row {
                id: caps; spacing: 4; visible: !k.recording; anchors.verticalCenter: parent.verticalCenter
                Repeater {
                    model: k.keys
                    delegate: Item {
                        required property string modelData
                        width: t.implicitWidth + 14; height: 20
                        Rectangle { anchors.fill: parent; radius: 5; color: "transparent"; border.color: theme.line; border.width: 1 }
                        Rectangle { x: 1; y: parent.height - 2.5; width: parent.width - 2; height: 1.5; radius: 1; color: theme.line }
                        Text { id: t; anchors.centerIn: parent; anchors.verticalCenterOffset: -0.5; text: modelData; font.pixelSize: 11; font.weight: Font.Medium; color: theme.fg3 }
                    }
                }
            }
            Item {
                id: rec; visible: k.recording; width: msg.implicitWidth + 20; height: 26; anchors.verticalCenter: parent.verticalCenter
                SequentialAnimation on opacity { running: k.recording; loops: Animation.Infinite
                    NumberAnimation { to: 0.4; duration: 700; easing.type: Easing.InOutSine } NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine } }
                Shape { id: dashed; anchors.fill: parent; layer.enabled: true; layer.samples: 4
                    ShapePath { strokeColor: theme.accent; strokeWidth: 1; fillColor: "transparent"; strokeStyle: ShapePath.DashLine; dashPattern: [3, 3]
                        PathSvg { path: Icons.rect(0.5, 0.5, dashed.width - 1, dashed.height - 1, 7.5) } } }
                Text { id: msg; anchors.centerIn: parent; text: "Press the new keys…"; font.pixelSize: 12; color: theme.accentFg }
            }
        }
        SButton { text: k.recording ? "Cancel" : "Change"; onClicked: k.toggled() }
    }
    // while recording, this item holds the keyboard focus and turns a pressed chord into portable text
    Item {
        id: capture; focus: k.recording
        onFocusChanged: if (focus) forceActiveFocus()
        Keys.onPressed: (e) => {
            if (!k.recording) return
            if (e.key === Qt.Key_Escape) { k.toggled(); e.accepted = true; return }
            var c = settings.chord(e.key, e.modifiers)
            if (c !== "") { k.chord(c); e.accepted = true }
        }
    }
    onRecordingChanged: if (recording) capture.forceActiveFocus()
}
