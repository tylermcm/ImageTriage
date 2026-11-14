import QtQuick
import QtQuick.Controls

Item {
    id: control
    property string label: ""
    property var editModel: null
    property real value: 0
    property real from: -100
    property real to: 100
    property var colors: [theme.s3, theme.s3]
    property bool compact: false
    property real step: 1
    property int decimals: 0
    property real controlHeight: 45
    property bool expandable: false
    property bool expanded: false
    readonly property bool pressed: slider.pressed       // the handle is being dragged
    signal committed(real next)
    signal toggleDetails()
    implicitHeight: compact ? 26 : controlHeight
    height: implicitHeight
    Text {
        visible: !control.compact
        text: control.label; color: theme.fg2
        font.pixelSize: 12
    }
    Rectangle {
        visible: !control.compact
        anchors.right: parent.right
        anchors.rightMargin: control.expandable ? 23 : 0
        width: 45; height: control.controlHeight < 40 ? 18 : 20; radius: 4
        color: theme.base; border.color: theme.lineSoft
        TextInput {
            id: numeric
            objectName: control.objectName + "_value"
            property bool cancelled: false
            anchors.fill: parent
            horizontalAlignment: TextInput.AlignHCenter
            verticalAlignment: TextInput.AlignVCenter
            color: activeFocus ? theme.fg : theme.fg2
            selectionColor: theme.accent; selectedTextColor: theme.onAccent
            font.pixelSize: 12
            selectByMouse: true
            validator: DoubleValidator { bottom: control.from; top: control.to; decimals: control.decimals; notation: DoubleValidator.StandardNotation; locale: "en_US" }
            onEditingFinished: {
                var next = Number(text)
                if (!cancelled && acceptableInput && Number.isFinite(next))
                    control.committed(next)
                cancelled = false
            }
            onAccepted: focus = false
            Keys.onEscapePressed: { cancelled = true; focus = false }
            Accessible.name: control.label + " value"
        }
        Binding {
            target: numeric; property: "text"
            value: slider.value.toFixed(control.decimals)
            when: !numeric.activeFocus
        }
    }
    Item {
        visible: control.expandable
        anchors.right: parent.right
        width: 20; height: 20
        Icon {
            anchors.centerIn: parent; size: 12; name: control.expanded ? "chevDown" : "chevR"
            color: theme.fg2
        }
        TapHandler { onTapped: control.toggleDetails() }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Accessible.role: Accessible.Button
        Accessible.name: control.label + " details"
        Accessible.onPressAction: control.toggleDetails()
    }
    Slider {
        id: slider
        objectName: control.objectName + "_slider"
        y: control.compact ? 0 : control.controlHeight < 40 ? 14 : 20
        width: parent.width; height: control.controlHeight < 40 && !control.compact ? 18 : 24; padding: 0
        from: control.from; to: control.to; stepSize: control.step
        value: control.value
        onMoved: control.committed(value)
        onPressedChanged: {
            if (pressed) { if (control.editModel) control.editModel.beginGesture() }
            else {
                control.committed(value)
                if (control.editModel) control.editModel.endGesture()
            }
        }
        background: Rectangle {
            y: parent.height / 2 - 1.5; width: parent.width; height: 3; radius: 1.5
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: control.colors[0] }
                GradientStop { position: 0.5; color: control.colors[Math.floor((control.colors.length - 1) / 2)] }
                GradientStop { position: 1; color: control.colors[control.colors.length - 1] }
            }
        }
        handle: Rectangle {
            x: slider.visualPosition * (slider.width - width); y: slider.height / 2 - 5
            width: 10; height: 10; radius: 5
            color: slider.pressed || slider.hovered ? theme.accentFg : theme.accent
            border.color: theme.line
        }
        Accessible.name: control.label
    }
}
