import QtQuick
import QtQuick.Controls

Column {
    id: panel
    required property var model
    required property var raw
    spacing: 10
    enabled: raw.loaded && !raw.saving && !raw.busy

    Text { text: "Red Eye"; color: theme.fg; font.pixelSize: 14; font.bold: true }
    Row {
        width: parent.width; spacing: 8
        Text { text: "Type"; width: 40; height: 30; verticalAlignment: Text.AlignVCenter; color: theme.fg2; font.pixelSize: 12 }
        Rectangle {
            width: parent.width-111; height: 30; radius: 8; color: theme.s3
            Text { anchors.centerIn: parent; text: "Red Eye"; color: theme.fg; font.pixelSize: 12 }
        }
        SButton { text: "Auto"; enabled: false; width: 55 }
    }
    Repeater {
        model: [{label:"Pupil size", key:"pupilSize", low:1}, {label:"Darken", key:"darken", low:0}]
        RawColorSlider {
            required property var modelData
            objectName: "redEyeOption_" + modelData.key
            width: parent.width; controlHeight: 32
            label: modelData.label; from: modelData.low; to: 100; step: 1; decimals: 0
            value: panel.model[modelData.key]
            onCommitted: (next) => panel.model.option(modelData.key, next)
        }
    }
    Text {
        width: parent.width; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 12
        text: panel.model.selected >= 0 ? "Adjusting Correction " + (panel.model.selected+1) + ". Click another pupil to add a correction."
              : "Click each red pupil to correct it. Zoom in for small eyes. Pupil size controls the search area around the click."
    }
    Row {
        spacing: 6
        SButton { text: "Next pupil"; enabled: panel.model.selected >= 0; onClicked: panel.model.select(-1) }
        SButton { text: "Clear"; enabled: panel.model.rows.length > 0; onClicked: panel.model.clear() }
    }
    Row {
        visible: panel.raw.busy; spacing: 8
        BusyIndicator { width: 22; height: 22; running: visible }
        Text { text: "Applying correction…"; color: theme.fg2; font.pixelSize: 12 }
    }
    Rectangle { width: parent.width; height: 1; color: theme.line }
    Text { text: panel.model.rows.length + (panel.model.rows.length === 1 ? " correction" : " corrections"); color: theme.fg2; font.pixelSize: 12 }
    Repeater {
        model: panel.model.rows
        Row {
            id: correction
            required property var modelData
            required property int index
            width: parent.width; spacing: 6
            SButton {
                objectName: "redEyeCorrection_" + correction.index
                width: parent.width-64; text: "Correction " + (correction.index+1)
                variant: panel.model.selected === correction.index ? "primary" : "normal"
                onClicked: panel.model.select(correction.index)
            }
            SButton { text: "Delete"; width: 58; onClicked: panel.model.remove(correction.index) }
        }
    }
    Text { width: parent.width; text: "Pet Eye and automatic correction are not available."; color: theme.fg3; font.pixelSize: 11; wrapMode: Text.WordWrap }
}
