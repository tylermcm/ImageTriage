import QtQuick
import QtQuick.Controls

Item {
    id: technical
    required property var model
    required property string section
    property bool sharpeningOpen: false
    property bool noiseOpen: false
    readonly property bool editable: model.loaded && !model.saving
    width: parent.width
    implicitHeight: content.implicitHeight
    clip: true
    layer.enabled: true

    component Copy: Text {
        font.family: "Segoe UI"; font.pixelSize: 12
        color: theme.fg2
    }
    component Divider: Rectangle {
        width: parent.width; height: 1; color: theme.lineSoft
    }
    component Choice: Row {
        property string label: ""
        property string value: ""
        width: parent.width; height: 30; spacing: 8
        Copy { id: choiceLabel; width: parent.label === "Process" ? 46 : 65; text: parent.label; anchors.verticalCenter: parent.verticalCenter }
        ComboBox {
            width: parent.width - choiceLabel.width - 8; height: 27
            model: [parent.value]; enabled: false
            contentItem: Copy { text: parent.displayText; leftPadding: 7; rightPadding: 20; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
            background: Rectangle { radius: 6; color: theme.s2; border.color: theme.lineSoft }
            indicator: Icon { x: parent.width - 18; anchors.verticalCenter: parent.verticalCenter; name: "chevDown"; size: 12; color: theme.fg3 }
            opacity: 0.65
        }
    }
    component Check: Item {
        id: check
        property string label: ""
        property bool available: false
        property bool checked: false
        signal toggled(bool next)
        width: parent.width; height: 24
        CheckBox {
            anchors.fill: parent
            enabled: check.available && technical.editable
            checked: check.checked
            onToggled: check.toggled(checked)
            spacing: 8; padding: 0
            indicator: Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 13; height: 13; radius: 3
                color: check.checked ? theme.accent : "transparent"
                border.color: check.available ? theme.fg3 : Qt.alpha(theme.fg3, 0.45)
                Copy { anchors.centerIn: parent; text: "✓"; color: theme.onAccent; visible: check.checked; font.pixelSize: 11 }
            }
            contentItem: Copy {
                text: check.label; leftPadding: 21; verticalAlignment: Text.AlignVCenter
                color: check.available ? theme.fg2 : Qt.alpha(theme.fg3, 0.7)
            }
            Accessible.name: check.label
        }
        HoverHandler { id: hover }
        ToolTip.text: available ? "Export at twice the resolution." : "Not available yet."
        ToolTip.visible: hover.hovered
    }
    component EmptySlider: Item {
        property string label: ""
        width: parent.width; height: 36
        Copy { text: parent.label; color: Qt.alpha(theme.fg3, 0.65) }
        Rectangle {
            anchors.right: parent.right; width: 43; height: 18; radius: 3
            color: "transparent"; border.color: theme.lineSoft
        }
        Rectangle { width: parent.width; height: 3; y: 27; radius: 1.5; color: theme.lineSoft }
        HoverHandler { id: hover }
        ToolTip.text: "Not available yet."; ToolTip.visible: hover.hovered
        Accessible.role: Accessible.Slider; Accessible.name: label + ", unavailable"
    }
    component Disclosure: Item {
        id: disclosure
        property string label: ""
        property bool available: false
        property bool expanded: false
        property string glyph: ""
        signal toggled()
        width: parent.width; height: 31
        Rectangle { visible: !disclosure.available; anchors.fill: parent; radius: 7; color: Qt.alpha(theme.lift, 0.025) }
        Icon { visible: !!disclosure.glyph; x: 7; anchors.verticalCenter: parent.verticalCenter; size: 17; path: disclosure.glyph; color: theme.fg3; opacity: 0.5 }
        Copy {
            x: disclosure.glyph ? 32 : 0; anchors.verticalCenter: parent.verticalCenter
            text: disclosure.label; color: disclosure.available ? theme.fg2 : Qt.alpha(theme.fg3, 0.65)
        }
        Icon { anchors.right: parent.right; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter; size: 12; name: disclosure.expanded ? "chevDown" : "chevR"; color: theme.fg3 }
        TapHandler { enabled: disclosure.available; onTapped: disclosure.toggled() }
        HoverHandler { id: hover; cursorShape: disclosure.available ? Qt.PointingHandCursor : Qt.ArrowCursor }
        ToolTip.text: "Not available yet."; ToolTip.visible: hover.hovered && !disclosure.available
        Accessible.role: Accessible.Button; Accessible.name: disclosure.label
        Accessible.onPressAction: if (disclosure.available) disclosure.toggled()
    }
    component ValueSlider: RawColorSlider {
        editModel: technical.model
        property string setting: ""
        objectName: "rawTechnical_" + setting
        width: parent.width; controlHeight: 32
        enabled: technical.editable
        value: technical.model.values[setting]
        onCommitted: (next) => technical.model.adjust(setting, next)
    }

    Rectangle { anchors.fill: parent; color: theme.light ? theme.s1 : "#151618" }
    Loader {
        id: content
        width: parent.width
        sourceComponent: technical.section === "Detail" ? detailPage
            : technical.section === "Optics" ? opticsPage
            : technical.section === "Lens Blur" ? lensPage : calibrationPage
    }
    Component {
        id: detailPage
        Column {
            width: parent.width; spacing: 7
            Choice { label: "Demosaic"; value: "AHD (adaptive homogeneity)" }
            Check { label: "Denoise" }
            EmptySlider { label: ""; height: 30 }
            Check { label: "Raw Details" }
            Check {
                objectName: "rawSuperResolution"
                label: "Super Resolution"; available: true
                checked: technical.model.values.superResolution
                onToggled: (next) => technical.model.setSuperResolution(next)
            }
            Divider {}
            ValueSlider {
                objectName: "rawDetailSharpen"
                label: "Sharpening"; setting: "sharpenAmount"; from: 0; to: 150
                expandable: true; expanded: technical.sharpeningOpen
                onToggleDetails: technical.sharpeningOpen = !technical.sharpeningOpen
            }
            Column {
                visible: technical.sharpeningOpen
                width: parent.width - 10; x: 10; spacing: 3
                ValueSlider { label: "Radius"; setting: "sharpenRadius"; from: 0.5; to: 3; step: 0.1; decimals: 1 }
                ValueSlider { label: "Detail"; setting: "sharpenDetail"; from: 0; to: 100 }
                ValueSlider { label: "Masking"; setting: "sharpenMasking"; from: 0; to: 100 }
            }
            Disclosure {
                objectName: "rawManualNoise"
                label: "Manual Noise Reduction"; available: true; expanded: technical.noiseOpen
                onToggled: technical.noiseOpen = !technical.noiseOpen
            }
            Column {
                visible: technical.noiseOpen
                width: parent.width - 10; x: 10; spacing: 3
                ValueSlider { label: "Luminance"; setting: "noiseLuminance"; from: 0; to: 100 }
                ValueSlider { label: "Luminance Detail"; setting: "noiseLuminanceDetail"; from: 0; to: 100 }
                ValueSlider { label: "Color"; setting: "noiseColor"; from: 0; to: 100 }
                ValueSlider { label: "Color Detail"; setting: "noiseColorDetail"; from: 0; to: 100 }
            }
        }
    }
    Component {
        id: opticsPage
        Column {
            width: parent.width; spacing: 8
            Row {
                height: 29; spacing: 16
                Copy { text: "Profile"; color: theme.fg; bottomPadding: 8; Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 2; color: theme.accent } }
                Copy { text: "Manual"; color: theme.fg3 }
            }
            Check { label: "Remove chromatic aberration" }
            Check { label: "Use profile corrections" }
            Divider {}
            Disclosure { label: "Defringe"; glyph: "M4 16h4v4H4zM8 12h4v4H8zM12 8h4v4h-4zM16 4h4v4h-4z" }
        }
    }
    Component {
        id: lensPage
        Column {
            width: parent.width; spacing: 10
            Check { label: "Apply" }
            EmptySlider { label: "Blur Amount" }
            Copy { text: "Bokeh"; color: theme.fg3 }
            Row {
                width: parent.width
                spacing: Math.max(2, (width - 5 * 34) / 4)
                Repeater {
                    model: [
                        "M8 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8m8-2a4 4 0 1 0 0 8 4 4 0 0 0 0-8m-3 7a4 4 0 1 0 0 8 4 4 0 0 0 0-8",
                        "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18M12 6a6 6 0 1 0 0 12 6 6 0 1 0 0-12",
                        "m12 3 9 7-4 11H7L3 10z",
                        "M10 3a7 7 0 1 0 0 14 7 7 0 1 0 0-14m4 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14",
                        "M7 4c-5 4-5 12 0 16 5-4 5-12 0-16m10 0c-5 4-5 12 0 16 5-4 5-12 0-16"
                    ]
                    delegate: Rectangle {
                        required property string modelData
                        width: 34; height: 34; radius: 9; color: "transparent"; border.color: theme.lineSoft
                        Icon { anchors.centerIn: parent; size: 25; path: parent.modelData; color: theme.fg3; opacity: 0.45 }
                    }
                }
            }
            EmptySlider { label: "Cat Eye" }
            EmptySlider { label: "Bokeh Boost" }
            Divider {}
            Item {
                width: parent.width; height: 24
                Copy { text: "Focus Range"; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
                Row {
                    anchors.right: parent.right; spacing: 8
                    Icon { size: 20; color: theme.fg3; opacity: 0.45; path: "M5 8V4h4m6 0h4v4M5 16v4h4m6 0h4v-4M9 9a3 3 0 1 0 6 0 3 3 0 1 0-6 0m-2 9c0-5 10-5 10 0" }
                    Icon { size: 20; color: theme.fg3; opacity: 0.45; path: "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18M12 5v4m0 6v4M5 12h4m6 0h4" }
                }
            }
            Rectangle { width: parent.width; height: 21; color: Qt.alpha(theme.fg3, 0.055) }
            Item {
                width: parent.width; height: 20
                Copy { text: "Near"; color: theme.fg3 }
                Copy { anchors.right: parent.right; text: "Far"; color: theme.fg3 }
            }
            Check { label: "Visualize Depth" }
            Disclosure { label: "Refinement"; glyph: "m4 17 11-11 3 3-11 11H4zM14 5l2-2 5 5-2 2" }
        }
    }
    Component {
        id: calibrationPage
        Column {
            width: parent.width; spacing: 10
            Choice { label: "Process"; value: "Version 6 (Current)" }
            Divider {}
            Copy { anchors.horizontalCenter: parent.horizontalCenter; text: "Shadows" }
            ValueSlider {
                objectName: "rawCalibrationTint"
                label: "Tint"; setting: "calibShadowTint"
                colors: ["#58b16c", "#a3a0a6", "#ba68b6"]
            }
            Repeater {
                model: [
                    {label: "Red Primary", hue: ["#ba68ba", "#b59a90", "#d39b64"], sat: ["#858a91", "#b7807b", "#d66b63"]},
                    {label: "Green Primary", hue: ["#b4b658", "#91ac79", "#58b594"], sat: ["#858a91", "#77a184", "#57b66d"]},
                    {label: "Blue Primary", hue: ["#59b8c3", "#878bb4", "#ad71cf"], sat: ["#858a91", "#7f8cae", "#6596d5"]}
                ]
                delegate: Column {
                    id: primary
                    required property var modelData
                    required property int index
                    width: technical.width; spacing: 3
                    topPadding: 8
                    Copy { anchors.horizontalCenter: parent.horizontalCenter; text: primary.modelData.label; bottomPadding: 5 }
                    RawColorSlider {
        editModel: technical.model
                        objectName: "rawCalibrationHue_" + primary.index
                        width: parent.width; controlHeight: 32; label: "Hue"
                        colors: primary.modelData.hue
                        enabled: technical.editable
                        value: technical.model.values.calibHue[primary.index]
                        onCommitted: (next) => technical.model.setCalibration(primary.index, "calibHue", next)
                    }
                    RawColorSlider {
        editModel: technical.model
                        objectName: "rawCalibrationSat_" + primary.index
                        width: parent.width; controlHeight: 32; label: "Saturation"
                        colors: primary.modelData.sat
                        enabled: technical.editable
                        value: technical.model.values.calibSat[primary.index]
                        onCommitted: (next) => technical.model.setCalibration(primary.index, "calibSat", next)
                    }
                }
            }
        }
    }
}
