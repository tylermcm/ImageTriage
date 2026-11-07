import QtQuick
import QtQuick.Controls

Column {
    id: grading
    required property var model
    property string view: "Three way"
    spacing: 10

    Row {
        height: 27; spacing: 5
        Text { text: "Adjust"; color: theme.fg2; font.family: "Segoe UI"; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter; rightPadding: 4 }
        Repeater {
            model: [
                {label: "Three way", glyph: "M9 5a3 3 0 1 0 6 0 3 3 0 0 0-6 0M3 16a3 3 0 1 0 6 0 3 3 0 0 0-6 0m12 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0"},
                {label: "Shadows", glyph: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16"},
                {label: "Midtones", glyph: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16M12 7v10"},
                {label: "Highlights", glyph: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16M8 12h8"},
                {label: "Global", glyph: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16M12 4v16"}
            ]
            delegate: Rectangle {
                required property var modelData
                width: 27; height: 27; radius: 6
                color: grading.view === modelData.label ? Qt.alpha(theme.accent, 0.18) : "transparent"
                Icon { anchors.centerIn: parent; size: 19; path: parent.modelData.glyph; color: grading.view === parent.modelData.label ? theme.accentFg : theme.fg2 }
                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: grading.view = modelData.label }
                ToolTip.text: modelData.label; ToolTip.visible: hover.hovered
                Accessible.role: Accessible.RadioButton; Accessible.name: modelData.label
                Accessible.checked: grading.view === modelData.label
                Accessible.onPressAction: grading.view = modelData.label
            }
        }
    }
    Column {
        visible: grading.view === "Three way"
        width: parent.width; spacing: 6
        RawGradingWheel { width: parent.width; model: grading.model; setting: "gradeMidtones"; label: "Midtones"; diameter: Math.min(110, parent.width * 0.38) }
        Row {
            width: parent.width; spacing: 14
            RawGradingWheel { width: (parent.width - 14) / 2; model: grading.model; setting: "gradeShadows"; label: "Shadows"; diameter: Math.min(104, width - 8) }
            RawGradingWheel { width: (parent.width - 14) / 2; model: grading.model; setting: "gradeHighlights"; label: "Highlights"; diameter: Math.min(104, width - 8) }
        }
    }
    RawGradingWheel {
        visible: grading.view !== "Three way"
        width: parent.width; model: grading.model; label: grading.view
        setting: grading.view === "Three way" ? "gradeGlobal" : "grade" + grading.view
        diameter: Math.min(190, parent.width - 38)
    }
    RawColorSlider {
        editModel: grading.model
        objectName: "rawGradeBlending"
        width: parent.width; label: "Blending"; from: 0; to: 100
        enabled: grading.model.loaded && !grading.model.saving
        value: grading.model.values.gradeBlending
        onCommitted: (next) => grading.model.adjust("gradeBlending", next)
    }
    RawColorSlider {
        editModel: grading.model
        objectName: "rawGradeBalance"
        width: parent.width; label: "Balance"
        enabled: grading.model.loaded && !grading.model.saving
        value: grading.model.values.gradeBalance
        onCommitted: (next) => grading.model.adjust("gradeBalance", next)
    }
}
