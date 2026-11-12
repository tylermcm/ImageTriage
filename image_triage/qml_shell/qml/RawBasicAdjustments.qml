import QtQuick
import QtQuick.Controls

Column {
    id: basic
    required property var model
    required property string section
    property bool picking: false
    property bool localAdjustments: false
    signal pickerToggled()
    spacing: 3
    clip: true
    layer.enabled: true
    enabled: model.loaded && !model.saving
    readonly property var lightFields: [
        {label: "Exposure", key: "exposure", low: -5, high: 5, step: 0.05, decimals: 2},
        {label: "Contrast", key: "contrast"}, {label: "Highlights", key: "highlights"},
        {label: "Shadows", key: "shadows"}, {label: "Whites", key: "whites"}, {label: "Blacks", key: "blacks"}
    ]
    readonly property var colorFields: [
        {label: "Temperature", key: "temperature", colors: ["#608fc3", "#a7a7a0", "#d3bd64"]},
        {label: "Tint", key: "tint", colors: ["#65a77a", "#a7a0a5", "#bd76b6"]},
        {label: "Vibrance", key: "vibrance", colors: ["#858a91", "#9aa887", "#cc7268"]},
        {label: "Saturation", key: "saturation", colors: ["#858a91", "#adac75", "#d06d65"]}
    ]
    readonly property var effectFields: [
        {label: "Texture", key: "texture"}, {label: "Clarity", key: "clarity"}, {label: "Dehaze", key: "dehaze"},
        {label: "Vignette", key: "vignetteAmount", details: true},
        {label: "Grain", key: "grainAmount", low: 0, details: true},
        {label: "Glow", key: "glow", low: 0, details: true}
    ]

    component StyleChoice: Row {
        id: choice
        property string effect: ""
        property var options: []
        property var labels: []
        width: parent.width; height: 32; spacing: 8
        Text {
            width: 60; anchors.verticalCenter: parent.verticalCenter
            text: "Style"; color: theme.fg2; font.pixelSize: 12
        }
        ComboBox {
            width: parent.width - 68; height: 27
            model: choice.labels
            currentIndex: choice.options.indexOf(choice.effect === "glow" ? basic.model.values.glow.style : basic.model.values.vignetteStyle)
            onActivated: basic.model.setEffectStyle(choice.effect, choice.options[currentIndex])
            contentItem: Text { text: parent.displayText; color: theme.fg; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 6; color: theme.s2; border.color: theme.lineSoft }
            indicator: Icon { x: parent.width - 19; anchors.verticalCenter: parent.verticalCenter; name: "chevDown"; size: 12; color: theme.fg2 }
        }
    }

    Row {
        visible: basic.section === "Color" && !basic.localAdjustments
        width: parent.width; height: visible ? 35 : 0; spacing: 8
        Text {
            id: balanceLabel
            anchors.verticalCenter: parent.verticalCenter
            text: "White balance"; color: theme.fg2; font.pixelSize: 12
        }
        ComboBox {
            objectName: "rawWhiteBalance"
            width: Math.max(30, parent.width - balanceLabel.implicitWidth - 20 - parent.spacing * 2); height: 27
            model: ["As Shot", "Custom"]
            currentIndex: basic.model.values.temperature === 0 && basic.model.values.tint === 0 ? 0 : 1
            onActivated: if (currentIndex === 0) basic.model.asShotWhiteBalance()
            delegate: ItemDelegate {
                required property int index
                required property string modelData
                width: ListView.view.width
                text: modelData; enabled: index === 0
                font.pixelSize: 12
            }
            contentItem: Text { text: parent.displayText; color: theme.fg; font.pixelSize: 12; leftPadding: 8; rightPadding: 20; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 6; color: theme.s2; border.color: theme.lineSoft }
            indicator: Icon { x: parent.width - 19; anchors.verticalCenter: parent.verticalCenter; name: "chevDown"; size: 12; color: theme.fg2 }
        }
        Item {
            objectName: "rawWhiteBalancePicker"
            width: 20; height: 27
            Rectangle { anchors.fill: parent; radius: 5; color: basic.picking ? Qt.alpha(theme.accent, 0.2) : "transparent" }
            Icon {
                anchors.centerIn: parent; size: 17; color: basic.picking ? theme.accentFg : theme.fg2
                path: "m14 4 6 6m-9-3 6 6M4 16l9-9 4 4-9 9H4zM15 5l2-2 4 4-2 2"
            }
            TapHandler { onTapped: basic.pickerToggled() }
            HoverHandler { id: pickerHover; cursorShape: Qt.PointingHandCursor }
            ToolTip.text: basic.picking ? "Choose a neutral area in the photo. Escape cancels." : "Pick white balance from the photo"
            ToolTip.visible: pickerHover.hovered
            Accessible.role: Accessible.Button
            Accessible.name: "White balance picker"
            Accessible.onPressAction: basic.pickerToggled()
        }
    }
    Repeater {
        model: basic.section === "Light" ? basic.lightFields : basic.section === "Color" ? basic.colorFields : (basic.localAdjustments ? basic.effectFields.slice(0,3) : basic.effectFields)
        delegate: Column {
            id: entry
            required property var modelData
            property bool detailsOpen: false
            width: basic.width; spacing: 3
            RawColorSlider {
        editModel: basic.model
                width: parent.width; controlHeight: 32
                objectName: (basic.localAdjustments ? "maskBasic_" : "rawBasic_") + entry.modelData.key
                label: entry.modelData.label
                from: entry.modelData.low === undefined ? -100 : entry.modelData.low
                to: entry.modelData.high === undefined ? 100 : entry.modelData.high
                step: entry.modelData.step || 1
                decimals: entry.modelData.decimals || 0
                colors: entry.modelData.colors || ["#606975", "#606975"]
                expandable: entry.modelData.details === true
                expanded: entry.detailsOpen
                value: entry.modelData.key === "glow" ? basic.model.values.glow.amount : basic.model.values[entry.modelData.key]
                onToggleDetails: entry.detailsOpen = !entry.detailsOpen
                onCommitted: (next) => {
                    if (entry.modelData.key === "glow") basic.model.setGlow("amount", next)
                    else basic.model.adjust(entry.modelData.key, next)
                }
            }
            Column {
                visible: entry.detailsOpen && entry.modelData.key === "vignetteAmount"
                width: parent.width - 10; x: 10; spacing: 3
                StyleChoice {
                    effect: "vignette"
                    options: ["highlightPriority", "colorPriority", "paintOverlay"]
                    labels: ["Highlight Priority", "Color Priority", "Paint Overlay"]
                }
                Repeater {
                    model: [
                        {label: "Midpoint", key: "vignetteMidpoint", low: 0},
                        {label: "Roundness", key: "vignetteRoundness", low: -100},
                        {label: "Feather", key: "vignetteFeather", low: 0},
                        {label: "Highlights", key: "vignetteHighlights", low: 0}
                    ]
                    delegate: RawColorSlider {
        editModel: basic.model
                        required property var modelData
                        width: parent.width; controlHeight: 32
                        label: modelData.label; from: modelData.low; to: 100
                        value: basic.model.values[modelData.key]
                        onCommitted: (next) => basic.model.adjust(modelData.key, next)
                    }
                }
            }
            Column {
                visible: entry.detailsOpen && entry.modelData.key === "grainAmount"
                width: parent.width - 10; x: 10; spacing: 3
                Repeater {
                    model: [{label: "Size", key: "grainSize"}, {label: "Roughness", key: "grainRoughness"}]
                    delegate: RawColorSlider {
        editModel: basic.model
                        required property var modelData
                        width: parent.width; controlHeight: 32
                        label: modelData.label; from: 0; to: 100
                        value: basic.model.values[modelData.key]
                        onCommitted: (next) => basic.model.adjust(modelData.key, next)
                    }
                }
            }
            Column {
                visible: entry.detailsOpen && entry.modelData.key === "glow"
                width: parent.width - 10; x: 10; spacing: 3
                StyleChoice { effect: "glow"; options: ["diffusion", "bloom", "halation"]; labels: ["Diffusion", "Bloom", "Halation"] }
                Repeater {
                    model: [{label: "Range", key: "range", low: 0}, {label: "Spread", key: "spread", low: 0}, {label: "Warmth", key: "warmth", low: -100}]
                    delegate: RawColorSlider {
        editModel: basic.model
                        required property var modelData
                        width: parent.width; controlHeight: 32
                        label: modelData.label; from: modelData.low; to: 100
                        value: basic.model.values.glow[modelData.key]
                        onCommitted: (next) => basic.model.setGlow(modelData.key, next)
                    }
                }
            }
        }
    }
}
