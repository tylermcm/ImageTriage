import QtQuick

// One setting: label and description on the left, its control on the right, a hairline below. `def` is a row from SettingsModel.page().
Item {
    id: row
    property var def
    readonly property bool dirty: { void settings.revision; return settings.isDirty(def.id) }
    readonly property bool usable: { void settings.revision; return settings.enabled(def.id) }
    readonly property var val: { void settings.revision; return settings.value(def.id) }
    readonly property bool searching: UiState.settingsQuery.trim() !== ""
    property string recording: ""                 // the shortcut id being recorded (one at a time, owned by the page)
    signal record(string id)
    width: parent ? parent.width : 700
    readonly property real boxH: Math.max(56, lab.height + 24 + 1)         // min-height 56 includes the hairline
    height: boxH
    opacity: usable ? 1 : 0.42
    Behavior on opacity { NumberAnimation { duration: 200 } }
    enabled: usable

    Rectangle { anchors.fill: parent; color: Qt.alpha(theme.lift, hh.hovered ? 0.03 : 0) }
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
    HoverHandler { id: hh }

    Rectangle {   // unsaved-change dot, hung in the left margin of the label
        x: -10; y: (row.boxH - 1 - lab.height) / 2 + 6.1; width: 6; height: 6; radius: 3; color: theme.accent
        opacity: row.dirty ? 1 : 0; scale: row.dirty ? 1 : 0.4
        Behavior on opacity { NumberAnimation { duration: 250 } }
        Behavior on scale { SpringAnimation { spring: 5; damping: 0.4; epsilon: 0.001 } }
    }

    Column {
        id: lab
        x: 4; y: (row.boxH - 1 - height) / 2
        width: row.width - 8 - ctrl.width - 24
        spacing: 2
        Row {
            spacing: 8
            Text { height: 18.2; verticalAlignment: Text.AlignVCenter; text: row.def.label; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
            Item {
                visible: row.searching; width: tag.implicitWidth + 14; height: 16; anchors.verticalCenter: parent.verticalCenter
                Rectangle { anchors.fill: parent; radius: 8; color: "transparent"; border.color: theme.lineSoft; border.width: 1 }
                Text { id: tag; anchors.centerIn: parent; text: row.def.section; font.pointSize: UiState.pt(10.5); font.weight: Font.Medium; color: theme.fg3 }
            }
        }
        Item {
            // the browser snaps each 17.4px line to whole pixels, which lands the lines 18px apart; Qt needs the 18 to match
            visible: row.def.desc !== ""; width: Math.min(460, parent.width); height: desc.lineCount * 17.4
            Text {
                id: desc; width: parent.width; wrapMode: Text.WordWrap; y: 1.3
                text: row.def.desc; color: theme.fg3; font.pixelSize: 12; lineHeight: 18; lineHeightMode: Text.FixedHeight
            }
        }
    }

    Row {
        id: ctrl
        anchors.right: parent.right; anchors.rightMargin: 4; y: Math.round((row.boxH - 1 - height) / 2)
        spacing: 10
        Loader {
            id: main; anchors.verticalCenter: parent.verticalCenter
            sourceComponent: ({ toggle: toggleC, select: selectC, step: stepC, slider: sliderC, swatch: swatchC, text: textC, keys: keysC })[row.def.type]
        }
        SButton {
            visible: row.def.btn !== "" && row.def.type !== "keys"; anchors.verticalCenter: parent.verticalCenter
            text: row.def.btn
            onClicked: { if (row.def.id === "gamma") settings.set("gamma", 1.0) }
        }
    }

    Component { id: toggleC; SSwitch { checked: !!row.val; onToggled: (v) => settings.set(row.def.id, v) } }
    Component { id: selectC
        SSelect { options: row.def.options; current: row.val; onPicked: (v) => settings.set(row.def.id, v) } }
    Component { id: stepC
        SStepper {
            text: { void settings.revision; return settings.display(row.def.id) }
            onDecrement: settings.set(row.def.id, row.val - row.def.step)
            onIncrement: settings.set(row.def.id, row.val + row.def.step)
        } }
    Component { id: sliderC
        SSlider {
            from: row.def.lo; to: row.def.hi; step: row.def.step; value: row.val
            text: { void settings.revision; return settings.display(row.def.id) }
            onMoved: (v) => settings.set(row.def.id, v)
        } }
    Component { id: swatchC
        SSwatches {
            hues: [["Blue", 255], ["Violet", 300], ["Rose", 10], ["Amber", 75], ["Green", 155], ["Teal", 195]]
            current: row.val; onPicked: (i) => settings.set(row.def.id, i)
        } }
    Component { id: textC
        Text {
            text: { void settings.revision; return settings.text(row.def.id) }
            horizontalAlignment: Text.AlignRight; width: Math.min(300, implicitWidth); wrapMode: Text.WordWrap
            color: theme.fg2; font.pixelSize: 12
        } }
    Component { id: keysC
        SKeys {
            keys: (row.val || "").split("+").filter(function (x, i, a) { return x !== "" || i === a.length - 1 })
            recording: row.recording === row.def.id
            onToggled: row.record(row.recording === row.def.id ? "" : row.def.id)
            onChord: (c) => { settings.set(row.def.id, c); row.record("") }
        } }
}
