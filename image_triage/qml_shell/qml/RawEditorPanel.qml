import QtQuick
import QtQuick.Controls

Rectangle {
    id: panel
    required property var model
    property string photoPath: ""
    property var capture: null
    property var metadata: ({})
    property bool before: false
    property bool whiteBalancePicking: false
    signal photoFocusRequested()
    property string tool: "Adjust"
    readonly property string displaySource: visible && model ? model.preview : ""
    readonly property color surface: theme.light ? theme.s1 : "#151618"
    readonly property color raised: theme.light ? theme.s2 : "#191b1e"
    readonly property color line: theme.light ? theme.line : "#2b2e33"
    readonly property color ink: theme.light ? theme.fg : "#e8ebf0"
    readonly property color muted: theme.light ? theme.fg2 : "#a3aab5"
    readonly property color subtle: theme.light ? theme.fg3 : "#848c99"
    readonly property color well: theme.light ? theme.base : "#111316"
    readonly property bool editable: !!model && model.loaded && !model.saving
    readonly property var groups: [
        {label: "Light", fields: []},
        {label: "Color", fields: []},
        {label: "Curve", fields: []},
        {label: "Color Mixer", fields: []},
        {label: "Color Grading", fields: []},
        {label: "Effects", fields: []},
        {label: "Detail", fields: []},
        {label: "Optics", fields: []},
        {label: "Lens Blur", fields: []},
        {label: "Calibration", fields: []}
    ]
    color: surface
    clip: true
    Rectangle { width: 1; height: parent.height; color: panel.line }
    function refreshCapture() {
        if (capture) { metadata = capture; return }
        metadata = photoPath && visible ? captureData.get(photoPath) : ({})
        if (photoPath && visible && !metadata.ready && !metadata.failed) captureData.request(photoPath)
    }
    onPhotoPathChanged: refreshCapture()
    onCaptureChanged: refreshCapture()
    onVisibleChanged: {
        if (!visible) { before = false; whiteBalancePicking = false; tool = "Adjust" }
        else refreshCapture()
        if (model) model.setCropEditing(visible && tool === "Crop")
    }
    Connections { target: captureData; function onReady(path) { if (path === panel.photoPath) panel.refreshCapture() } }
    onToolChanged: {
        if (model) model.setCropEditing(visible && tool === "Crop")
        whiteBalancePicking = false
        if (scroll.contentItem) scroll.contentItem.contentY = 0
    }

    component Copy: Text {
        color: panel.ink
        font.family: "Segoe UI"
        font.pixelSize: 13
        renderType: Text.NativeRendering
    }
    component Action: Button {
        id: action
        property string glyph: ""
        property bool primary: false
        property bool quiet: true
        property bool selected: false
        property bool pending: false
        implicitWidth: glyph ? 32 : Math.ceil(contentItem.implicitWidth) + 22
        implicitHeight: 35
        padding: 0
        hoverEnabled: true
        contentItem: Item {
            implicitWidth: action.glyph ? 16 : caption.implicitWidth
            Copy {
                id: caption
                visible: !action.glyph
                anchors.centerIn: parent; text: action.text
                color: action.primary ? "#101b2a" : action.selected ? "#a8ceff" : panel.ink
                font.weight: action.primary ? Font.DemiBold : Font.Normal
            }
            Icon { visible: !!action.glyph; anchors.centerIn: parent; size: 16; path: action.glyph; color: panel.ink }
        }
        background: Rectangle {
            radius: 8
            color: action.selected ? "#20334e" : action.primary ? theme.accent : action.hovered ? (theme.light ? theme.s3 : "#22252a") : action.quiet ? "transparent" : panel.raised
            border.color: action.selected ? "#3a5b85" : action.primary ? theme.accent : panel.line
        }
        opacity: enabled || pending ? 1 : 0.35
        Accessible.name: text
    }
    component Field: Item {
        id: field
        property string label: ""
        property string key: label.toLowerCase()
        readonly property var bounds: ({
            "exposure": [-5, 5, 0.05, 0],
            "grain": [0, 100, 1, 0], "glow": [0, 100, 1, 0],
            "sharpening": [0, 150, 1, 0], "radius": [0, 3, 0.1, 1],
            "noise reduction": [0, 100, 1, 0], "color noise": [0, 100, 1, 0],
            "defringe": [0, 100, 1, 0], "blur amount": [0, 100, 1, 0],
            "focus range": [0, 100, 1, 50], "size": [4, 100, 1, 28],
            "opacity": [0, 100, 1, 100], "pupil size": [0, 100, 1, 50],
            "darken": [0, 100, 1, 50], "amount": [0, 100, 1, 100],
            "scale": [50, 150, 1, 100]
        })[key] || [-100, 100, 1, 0]
        property real low: bounds[0]
        property real high: bounds[1]
        property real step: bounds[2]
        property real initialValue: bounds[3]
        readonly property bool wired: !!panel.model && panel.model.values[key] !== undefined
        width: parent.width; height: 28
        Copy { x: 0; anchors.verticalCenter: parent.verticalCenter; text: field.label; font.pixelSize: 12 }
        Slider {
            id: slider
            objectName: "rawSlider_" + field.key
            x: 89; width: Math.max(20, parent.width - 145); height: 28; padding: 0
            enabled: panel.editable && field.wired
            from: field.low; to: field.high; stepSize: field.step
            value: field.wired ? panel.model.values[field.key] : field.initialValue
            onPressedChanged: if (!pressed && enabled) { panel.before = false; panel.model.adjust(field.key, value) }
            onMoved: if (!pressed && enabled) { panel.before = false; panel.model.adjust(field.key, value) }
            background: Rectangle { y: 12.5; width: parent.width; height: 3; radius: 1.5; color: theme.light ? theme.s3 : "#373d45" }
            handle: Rectangle {
                x: slider.visualPosition * (slider.width - width)
                y: 9; width: 10; height: 10; radius: 5
                color: slider.hovered ? "#bddeff" : "#91bfff"
            }
        }
        Rectangle {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: 49; height: 21; radius: 4; color: panel.well
            Copy {
                anchors.right: parent.right; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter
                font.pixelSize: 12; color: panel.muted
                text: field.key === "exposure" ? (slider.value >= 0 ? "+" : "") + slider.value.toFixed(2) : slider.value.toFixed(0)
            }
        }
        HoverHandler { id: fieldHover }
        ToolTip.visible: !wired && fieldHover.hovered
        ToolTip.text: "This adjustment is not connected yet."
        ToolTip.delay: 500
    }
    component Section: RawAdjustmentSection {}
    component Choice: Row {
        id: choice
        property string label: ""
        property var choices: []
        width: parent.width; height: 35; spacing: label ? 8 : 0
        Copy { id: choiceLabel; anchors.verticalCenter: parent.verticalCenter; text: parent.label; color: panel.ink }
        ComboBox {
            model: parent.choices
            enabled: false
            width: Math.min(160, choice.width - choiceLabel.implicitWidth - (choice.label ? choice.spacing : 0)); height: 30
            font.family: "Segoe UI"; font.pixelSize: 12
            contentItem: Copy { text: parent.displayText; leftPadding: 11; rightPadding: 22; verticalAlignment: Text.AlignVCenter }
            indicator: Icon { x: parent.width - 20; anchors.verticalCenter: parent.verticalCenter; size: 12; path: "m6 9 6 6 6-6"; color: panel.ink }
            background: Rectangle { color: panel.raised; radius: 8; border.color: panel.line }
            opacity: 1
        }
    }
    component Check: Row {
        property string label: ""
        property bool checked: false
        width: parent.width; height: 28; topPadding: 9; spacing: 7
        Rectangle { width: 12; height: 12; anchors.verticalCenter: parent.verticalCenter; radius: 2; color: parent.checked ? theme.accent : panel.raised; border.color: panel.subtle
            Copy { anchors.centerIn: parent; text: "\u2713"; color: panel.well; font.pixelSize: 11; visible: parent.parent.checked }
        }
        Copy { text: parent.label; color: panel.muted; anchors.verticalCenter: parent.verticalCenter }
    }
    component PageTitle: Copy {
        width: parent.width; height: 29.03
        font.pixelSize: 15; font.weight: Font.Bold
    }
    component Pending: Copy {
        width: parent.width
        topPadding: 10; bottomPadding: 10
        text: "This tool is not available yet."
        font.pixelSize: 11; color: panel.subtle
        wrapMode: Text.WordWrap
    }

    Item {
        id: header
        objectName: "rawEditorHeader"
        x: 1; width: parent.width - 1; height: 161.78
        Item {
            x: 14; y: 13; width: parent.width - 28; height: 35
            Copy { text: "Adjustments"; font.pixelSize: 15; font.weight: Font.Bold; anchors.verticalCenter: parent.verticalCenter }
            Row {
                anchors.right: parent.right; spacing: 8
                Action {
                    objectName: "rawUndo"; text: "Undo"; width: 32; height: 32
                    glyph: "M8 5l-5 5 5 5m-5-5h12a5 5 0 0 1 0 10"
                    enabled: !!panel.model && panel.model.canUndo
                    onClicked: { panel.before = false; panel.model.undo() }
                    ToolTip.text: text; ToolTip.visible: hovered
                }
                Action {
                    objectName: "rawRedo"; text: "Redo"; width: 32; height: 32
                    glyph: "m16 5 5 5-5 5m5-5H9a5 5 0 0 0 0 10"
                    enabled: !!panel.model && panel.model.canRedo
                    onClicked: { panel.before = false; panel.model.redo() }
                    ToolTip.text: text; ToolTip.visible: hovered
                }
                Action {
                    objectName: "rawReset"; text: "Reset"
                    enabled: panel.editable
                    onClicked: { panel.before = false; panel.model.reset() }
                }
            }
        }
        Rectangle {
            x: 14; y: 55; width: parent.width - 28; height: 77; radius: 7
            color: theme.light ? theme.base : "#101215"; border.color: panel.line
            Histogram {
                objectName: "rawHistogram"
                anchors.fill: parent; anchors.margins: 1; three: true; scopeStyle: true
                service: panel.model ? panel.model.histograms : null
                path: panel.visible && panel.model ? (panel.before ? panel.model.baselinePath : panel.model.histogramPath) : ""
            }
        }
        Item {
            x: 14; y: 138; width: parent.width - 28; height: 16
            readonly property real gap: Math.max(0, (width - isoText.width - focalText.width - apertureText.width - shutterText.width) / 3)
            Copy {
                id: isoText
                text: panel.metadata.ready ? panel.metadata.iso || "\u2014" : "\u2014"
                color: panel.muted; font.pixelSize: 11
            }
            Copy {
                id: focalText; x: isoText.width + parent.gap
                text: panel.metadata.ready ? panel.metadata.focal || "\u2014" : "\u2014"
                color: panel.muted; font.pixelSize: 11
            }
            Copy {
                id: apertureText; x: focalText.x + focalText.width + parent.gap
                text: panel.metadata.ready ? panel.metadata.aperture || "\u2014" : "\u2014"
                color: panel.muted; font.pixelSize: 11
            }
            Copy {
                id: shutterText; anchors.right: parent.right
                text: panel.metadata.ready ? panel.metadata.shutter || "\u2014" : "\u2014"
                color: panel.muted; font.pixelSize: 11
            }
        }
    }
    Row {
        id: tools
        objectName: "rawToolRow"
        x: 8; y: header.height; width: parent.width - 15; height: 59.05; spacing: 1
        Repeater {
            model: [
                {label: "Adjust", path: "M4 6h16M4 12h16M4 18h16M8 4v4m8 2v4m-6 2v4"},
                {label: "Crop", path: "M7 3v14h14M3 7h14v14"},
                {label: "Mask", path: "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18m0 0v18"},
                {label: "Remove", path: "m4 15 9-10a2 2 0 0 1 3 0l4 4a2 2 0 0 1 0 3l-7 8H8zM8 11l8 7"},
                {label: "Red Eye", path: "M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12zm10-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"},
                {label: "Presets", path: "m12 2 2.6 6.4L21 11l-6.4 2.6L12 20l-2.6-6.4L3 11l6.4-2.6z"},
                {label: "History", path: "M4 12a8 8 0 1 1 2 5m-2-5V5m0 7h6m2-5v5l3 2"}
            ]
            delegate: Rectangle {
                id: tab
                required property var modelData
                objectName: "rawTool_" + modelData.label
                y: 5; width: (tools.width - 6) / 7; height: 46; radius: 8
                color: panel.tool === modelData.label ? "#20334e" : tabHover.hovered ? panel.raised : "transparent"
                border.color: panel.tool === modelData.label ? "#3a5b85" : "transparent"
                Icon { anchors.horizontalCenter: parent.horizontalCenter; y: 7; size: 15; path: tab.modelData.path; color: panel.tool === tab.modelData.label ? "#a8ceff" : panel.muted }
                Copy { y: 25; width: parent.width; text: tab.modelData.label; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 9; color: panel.tool === tab.modelData.label ? "#a8ceff" : panel.muted }
                HoverHandler { id: tabHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: panel.tool = tab.modelData.label }
                Accessible.role: Accessible.Button; Accessible.name: modelData.label
                Accessible.onPressAction: panel.tool = modelData.label
            }
        }
    }
    Rectangle { x: 1; y: tools.y + tools.height - 1; width: parent.width - 1; height: 1; color: panel.line }
    ScrollView {
        id: scroll
        objectName: "rawEditorScroll"
        x: 1; y: tools.y + tools.height
        width: parent.width - 1; height: footer.y - y
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AlwaysOn
        ScrollBar.vertical.contentItem: Rectangle { implicitWidth: 4; radius: 2; color: "#3c424a"; opacity: parent.active ? 1 : 0.5 }
        Column {
            width: scroll.availableWidth - 10
            Column {
                visible: panel.tool === "Adjust"; width: parent.width
                Item {
                    width: parent.width; height: 52
                    Copy { x: 13; text: "RAW PROFILE"; anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; color: panel.muted }
                    Choice { x: parent.width - 127; width: 114; anchors.verticalCenter: parent.verticalCenter; choices: ["Embedded", "Neutral", "Vivid", "Monochrome"] }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: panel.line }
                }
                Repeater {
                    model: panel.groups
                    delegate: Section {
                        required property var modelData
                        title: modelData.label
                        expanded: title === "Light" || title === "Color"
                        Loader {
                            id: basicLoader
                            width: parent.width
                            active: parent.parent.expanded && ["Light", "Color", "Effects"].indexOf(parent.parent.title) >= 0
                            visible: active
                            sourceComponent: RawBasicAdjustments {
                                model: panel.model; section: basicLoader.parent.parent.title
                                picking: panel.whiteBalancePicking
                                onPickerToggled: {
                                    panel.before = false
                                    panel.whiteBalancePicking = !panel.whiteBalancePicking
                                    panel.photoFocusRequested()
                                }
                            }
                        }
                        Loader {
                            id: technicalLoader
                            width: parent.width
                            active: parent.parent.expanded && ["Detail", "Optics", "Lens Blur", "Calibration"].indexOf(parent.parent.title) >= 0
                            visible: active
                            sourceComponent: RawTechnicalAdjustments { model: panel.model; section: technicalLoader.parent.parent.title }
                        }
                        Loader {
                            width: parent.width
                            active: parent.parent.expanded && parent.parent.title === "Curve"
                            visible: active
                            sourceComponent: RawCurve { model: panel.model }
                        }
                        Loader {
                            width: parent.width
                            active: parent.parent.expanded && parent.parent.title === "Color Mixer"
                            visible: active
                            sourceComponent: RawColorMixer { model: panel.model }
                        }
                        Loader {
                            width: parent.width
                            active: parent.parent.expanded && parent.parent.title === "Color Grading"
                            visible: active
                            sourceComponent: RawColorGrading { model: panel.model }
                        }
                        Repeater { model: modelData.fields; delegate: Field { required property string modelData; label: modelData } }
                    }
                }
            }
            Column {
                visible: panel.tool === "Crop"; x: 13; width: parent.width - 26; topPadding: 12
                PageTitle { text: "Crop & Expand" }
                Copy { width: parent.width; text: "Drag on the photo to draw a crop. Hold Ctrl for a free crop. Drag inside to move it, or drag a corner to resize."; wrapMode: Text.WordWrap; color: panel.muted; font.pixelSize: 12; bottomPadding: 12 }
                SSelect { objectName: "rawCropRatio"; width: parent.width; options: ["Original", "Free", "1:1", "4:5", "3:2", "16:9"].map(function (v) { return {label: v, value: v} }); current: panel.model.values.hostCrop.ratio; enabled: panel.editable; onPicked: (value) => panel.model.setCropRatio(value) }
                Action { text: "Reset crop"; enabled: panel.editable; onClicked: panel.model.resetCrop() }
                Field { label: "Angle"; low: -45; high: 45; step: 0.1 }
                Grid {
                    width: parent.width; columns: 2; spacing: 6
                    Repeater {
                        model: ["\u21b6 Rotate left", "\u21b7 Rotate right", "Flip horizontal", "Flip vertical"]
                        delegate: Action { required property string modelData; text: modelData; width: (parent.width - 6) / 2; pending: true; enabled: false }
                    }
                }
                Section {
                    inset: 0; topPadding: 8
                    title: "Geometry"; expanded: true
                    Repeater { model: ["Projection", "Vertical", "Horizontal", "Rotate", "Aspect", "Scale", "Offset X", "Offset Y"]; delegate: Field { required property string modelData; label: modelData; low: label === "Scale" ? 50 : -100; high: label === "Scale" ? 150 : 100 } }
                }
                Section {
                    inset: 0; topPadding: 8; title: "Anamorphic"; Field { label: "Squeeze" } }
                Section {
                    inset: 0; topPadding: 8; title: "Generative Expand"; Pending {} }
                Section {
                    inset: 0; topPadding: 8; title: "Trim"; Pending {} }
                Copy { width: parent.width; text: "Crop is ready. Rotation, geometry and expansion are not available yet."; wrapMode: Text.WordWrap; color: panel.subtle; font.pixelSize: 11; topPadding: 12 }
            }
            RawMaskPanel {
                objectName: "rawMaskPanel"
                visible: panel.tool === "Mask"; x: 13; width: parent.width-26; topPadding: 12
                model: panel.model.maskEditor; raw: panel.model
            }
            RawRetouchPanel {
                objectName: "rawRetouchPanel"
                visible: panel.tool === "Remove"; x: 13; width: parent.width-26; topPadding: 12
                model: panel.model.retouchEditor; raw: panel.model
            }
            RawRedEyePanel {
                objectName: "rawRedEyePanel"
                visible: panel.tool === "Red Eye"; x: 13; width: parent.width-26; topPadding: 12
                model: panel.model.redEyeEditor; raw: panel.model
            }
            Column {
                visible: panel.tool === "Presets"; x: 13; width: parent.width - 26; topPadding: 12
                PageTitle { text: "Presets" }
                Field { label: "Amount"; low: 0; high: 200 }
                Repeater {
                    model: [
                        {name: "Adaptive", items: ["Portrait", "Sky", "Subject", "Landscape", "Blur Background"]},
                        {name: "Portraits", items: ["Soft portrait", "High contrast B&W"]},
                        {name: "Seasons", items: ["Spring", "Summer", "Warm woodland", "Winter"]},
                        {name: "Style", items: ["Soft matte", "Cinematic", "Film-inspired", "Vintage"]},
                        {name: "Subject", items: ["Food", "Landscape", "Travel", "Urban Architecture"]},
                        {name: "Defaults", items: ["Natural"]}, {name: "Curve", items: ["Soft matte"]},
                        {name: "Grain", items: ["Fine grain"]}, {name: "Optics", items: ["Lens correction"]}
                    ]
                    delegate: Section {
                    inset: 0; topPadding: 8
                        required property var modelData
                        title: modelData.name; expanded: title === "Seasons"
                        Repeater { model: modelData.items; delegate: Copy { required property string modelData; text: modelData; width: parent.width; height: 36.84; verticalAlignment: Text.AlignVCenter } }
                    }
                }
                Pending {}
            }
            Column {
                visible: panel.tool === "History"; x: 13; width: parent.width - 16; topPadding: 12
                PageTitle { text: "History" }
                Copy { width: parent.width; text: "Return to a previous adjustment on this photo."; color: panel.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; bottomPadding: 10 }
                Action { text: "Current state"; selected: true; width: parent.width }
                Repeater {
                    model: panel.model ? panel.model.history : []
                    delegate: Action {
                        required property string modelData
                        required property int index
                        text: "Before " + modelData; width: parent.width
                        enabled: panel.editable
                        onClicked: { panel.before = false; panel.model.restoreHistory(index) }
                    }
                }
            }
        }
    }
    Rectangle {
        id: footer
        objectName: "rawEditorFooter"
        x: 1; width: parent.width - 1; height: 55.84
        anchors.bottom: parent.bottom
        color: panel.surface
        Rectangle { width: parent.width; height: 1; color: panel.line }
        Copy {
            x: 10; width: Math.max(0, actions.x - 17)
            anchors.verticalCenter: parent.verticalCenter
            font.pixelSize: 11; color: panel.model && panel.model.error ? theme.rej : panel.subtle
            text: !panel.model ? "" : panel.model.error || (panel.model.saving ? "Saving edits\u2026" : panel.model.detailPending ? "Refining full-resolution detail\u2026" : panel.model.busy ? "Rendering preview\u2026" : panel.model.dirty ? "Unsaved edits" : "Original RAW")
            elide: Text.ElideRight
            HoverHandler { id: statusHover }
            ToolTip.text: text; ToolTip.visible: statusHover.hovered
        }
        Row {
            id: actions
            anchors.right: parent.right; anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter; spacing: 7
            Action { text: "Cancel"; enabled: !!panel.model && !panel.model.saving; onClicked: panel.model.cancel() }
            Action { text: "Done"; primary: true; enabled: panel.editable; onClicked: panel.model.done() }
        }
    }
}
