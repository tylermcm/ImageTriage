import QtQuick
import QtQuick.Controls

Column {
    id: panel
    required property var model
    required property var raw
    property bool editable: raw.loaded && !raw.saving && !model.ai.busy
    spacing: 8
    readonly property var current: model.current
    onVisibleChanged: { if (visible) model.ai.openMasking(); else model.ai.closeMasking() }
    component Copy: Text { color: theme.fg2; font.family: "Segoe UI"; font.pixelSize: 12; wrapMode: Text.WordWrap }
    component Action: Button {
        id: action
        property bool selected: false
        enabled: panel.editable
        hoverEnabled: true
        height: 35; implicitWidth: label.implicitWidth+22
        contentItem: Text { id: label; text: action.text; color: action.selected ? "#a8ceff" : theme.fg; font.pixelSize: 13; font.family: "Segoe UI"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        background: Rectangle { radius: 8; color: action.selected ? "#20334e" : action.hovered ? (theme.light ? theme.s3 : "#22252a") : "transparent"; border.color: action.selected ? "#3a5b85" : (theme.light ? theme.line : "#2b2e33") }
        opacity: enabled ? 1 : .4
    }
    QtObject {
        id: localModel
        readonly property bool loaded: panel.raw.loaded && panel.model.selected>=0
        readonly property bool saving: panel.raw.saving || panel.model.ai.busy
        signal changed()
        readonly property var histograms: panel.raw.histograms
        readonly property string histogramPath: panel.raw.histogramPath
        function setCurve(channel,points) { panel.model.setCurve(channel,points) }
        function setMixer(channel,component,value) { panel.model.setMixer(channel,component,value) }
        function setGrading(wheel,hue,sat,lum) { panel.model.setGrading(wheel,hue,sat,lum) }
        function setCalibration(channel,component,value) { panel.model.setCalibration(channel,component,value) }
        readonly property var values: Object.assign({},panel.raw.values,panel.current.adjust || ({}))
        function adjust(key,value) { panel.model.adjust(key,value) }
        function beginGesture() { panel.raw.beginGesture() }
        function endGesture() { panel.raw.endGesture() }
    }
    Connections { target: panel.model; function onChanged() { localModel.changed() } }
    component Field: RawColorSlider {
        required property string key
        property real low: -100
        property real high: 100
        property real currentValue: key === "amount" ? (panel.current.amount ?? 100) : (panel.current.shape || ({}))[key] || 0
        objectName: "maskField_"+key
        width: parent.width; controlHeight: 32; editModel: panel.raw
        enabled: panel.editable && panel.model.selected>=0
        from: low; to: high; value: currentValue; decimals: step<1 ? 2 : 0
        colors: ["#606975","#606975"]
        onCommitted: (next) => panel.model.adjust(key,next)
    }
    readonly property bool creating: model.selected < 0 && !model.tool
    readonly property string shapeKind: current.shape?.type || ""
    property bool rangeOpen: false
    onCreatingChanged: rangeOpen = false
    component ToolSlider: RawColorSlider {
        property real minimum: 0
        signal edited(real value)
        width: parent.width; controlHeight: 32; editModel: panel.raw
        from: minimum; to: 100; colors: ["#606975","#606975"]
        onCommitted: (next) => edited(next)
    }
    component Hero: Button {
        id: hero
        required property string title
        required property string iconPath
        required property string kind
        width: (parent.width-12)/3; height: 77; hoverEnabled: true
        objectName: "aiMaskTool_"+kind
        enabled: panel.editable && panel.model.ai.availability[kind] === "" && panel.model.rows.length < 16
        contentItem: Item {
            Icon { anchors.horizontalCenter: parent.horizontalCenter; y: 9; size: 27; path: hero.iconPath; color: theme.fg2 }
            Copy { anchors.horizontalCenter: parent.horizontalCenter; y: 43; text: hero.title; color: theme.fg }
        }
        background: Rectangle { radius: 9; color: hero.hovered ? theme.s3 : theme.s1; border.color: theme.line }
        opacity: enabled ? 1 : .4
        onClicked: panel.model.ai.start(kind)
        ToolTip.visible: hovered && !!panel.model.ai.availability[kind]
        ToolTip.text: panel.model.ai.availability[kind] || ""
    }
    Row {
        visible: panel.model.ai.busy; width: parent.width; spacing: 8
        BusyIndicator { running: visible; width: 26; height: 26 }
        Copy { width: parent.width-110; text: panel.model.ai.message; anchors.verticalCenter: parent.verticalCenter }
        Action { text: "Cancel"; enabled: true; onClicked: panel.model.ai.cancel() }
    }
    Copy { visible: !!panel.model.ai.error; width: parent.width; text: panel.model.ai.error; color: theme.rej }
    RawMaskOptions { width: parent.width; model: panel.model.ai }
    Column {
        visible: panel.creating && !panel.model.ai.optionsOpen; width: parent.width; spacing: 8
        Copy { text: "Create New Mask"; color: theme.fg; font.pixelSize: 14; font.bold: true }
        Row {
            width: parent.width; spacing: 6
            Hero { title: "Subject"; kind: "subject"; iconPath: "M12 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM5 21v-2a7 7 0 0 1 14 0v2" }
            Hero { title: "Sky"; kind: "sky"; iconPath: "M3 4h18v16H3zM3 16l5-5 5 5 3-3 5 5M15 8h3" }
            Hero { title: "Background"; kind: "background"; iconPath: "M3 3h18v18H3zM5 19l5-7 4 5 3-3 4 5" }
        }
        Rectangle { width: parent.width; height: 1; color: theme.line }
        Action { objectName: "aiMaskTool_scene"; width: parent.width; text: "Landscape  ›"; enabled: panel.editable && panel.model.ai.availability.scene === ""; onClicked: panel.model.ai.start("scene") }
        Action { objectName: "aiMaskTool_people"; visible: panel.model.ai.peopleCount > 0; width: parent.width; text: "People · "+panel.model.ai.peopleCount+"  ›"; onClicked: panel.model.ai.start("people") }
        Action { objectName: "aiMaskTool_object"; width: parent.width; text: "Objects"; enabled: panel.editable && panel.model.ai.availability.object === ""; onClicked: panel.model.ai.start("object") }
        Action { objectName: "maskTool_brush"; width: parent.width; text: "Brush"; onClicked: panel.model.arm("brush") }
        Action { objectName: "maskTool_linear"; width: parent.width; text: "Linear Gradient"; onClicked: panel.model.arm("linear") }
        Action { objectName: "maskTool_radial"; width: parent.width; text: "Radial Gradient"; onClicked: panel.model.arm("radial") }
        Action { width: parent.width; text: "Range  ⌄"; onClicked: panel.rangeOpen=!panel.rangeOpen }
        Row {
            visible: panel.rangeOpen; width: parent.width; spacing: 6
            Action { width: (parent.width-6)/2; text: "Color Range"; onClicked: panel.model.arm("colorRange") }
            Action { objectName: "maskTool_luminanceRange"; width: (parent.width-6)/2; text: "Luminance Range"; onClicked: panel.model.arm("luminanceRange") }
        }
        Rectangle { width: parent.width; height: 1; color: theme.line }
        Copy { text: "People"; color: theme.fg; font.bold: true; topPadding: 6 }
        Row {
            width: parent.width; spacing: 6
            BusyIndicator { visible: panel.model.ai.peopleScanning; running: visible; width: 22; height: 22 }
            Copy { width: parent.width-(panel.model.ai.peopleScanning ? 28 : 0); text: panel.model.ai.peopleMessage || "Checking for people when the RAW preview is ready…" }
        }
        Copy { width: parent.width; text: "Choose a tool to select the area you want to adjust."; color: theme.fg3; font.pixelSize: 11; topPadding: 10 }
        Action { text: "Check for people again"; visible: !panel.model.ai.peopleScanning && panel.model.ai.peopleCount===0; onClicked: panel.model.ai.retryPeople() }
    }
    Column {
        visible: !panel.creating && !panel.model.ai.optionsOpen; width: parent.width; spacing: 10
        Copy { text: panel.model.tool === "brush" ? "Brush" : panel.shapeKind === "colorRange" || panel.model.tool === "colorRange" ? "Color Range" : panel.shapeKind === "luminanceRange" ? "Luminance Range" : panel.current.name || "Draw a mask"; color: theme.fg; font.pixelSize: 14; font.bold: true }
        Column {
            visible: panel.model.tool === "brush"; width: parent.width; spacing: 5
            Row { spacing: 6; Action { text: "Paint"; selected: !panel.model.erasing; onClicked: panel.model.brushOptions(panel.model.brushSize,panel.model.brushFeather,false) } Action { text: "Erase"; selected: panel.model.erasing; onClicked: panel.model.brushOptions(panel.model.brushSize,panel.model.brushFeather,true) } }
            ToolSlider { label: "Size"; minimum: 1; value: panel.model.brushSize; onEdited: (value) => panel.model.brushOptions(value,panel.model.brushFeather,panel.model.erasing) }
            ToolSlider { label: "Feather"; value: panel.model.brushFeather; onEdited: (value) => panel.model.brushOptions(panel.model.brushSize,value,panel.model.erasing) }
            ToolSlider { label: "Flow"; value: panel.model.brushFlow; onEdited: (value) => panel.model.brushDynamics(value,panel.model.brushDensity) }
            ToolSlider { label: "Density"; value: panel.model.brushDensity; onEdited: (value) => panel.model.brushDynamics(panel.model.brushFlow,value) }
            Copy { width: parent.width; text: "Paint on the photo. Erase removes coverage from this mask." }
        }
        Column {
            visible: panel.shapeKind === "colorRange" || panel.model.tool === "colorRange"; width: parent.width; spacing: 8
            Row { width: parent.width; Copy { text: "Sampled color"; width: parent.width-24 } Rectangle { width: 18; height: 18; radius: 9; color: panel.model.sampleSwatch; border.color: theme.line } }
            Field { label: "Refine"; key: "range"; low: 0; high: 100 }
            Action { text: "Sample on photo"; onClicked: panel.model.sampleAgain() }
            Copy { width: parent.width; text: "Click to sample a color. Drag to average an area. Shift-click adds another sample." }
        }
        Column {
            visible: panel.shapeKind === "luminanceRange"; width: parent.width; spacing: 6
            Copy { text: "Select Luminance" }
            RangeSlider {
                id: luminance
                objectName: "maskLuminanceRange"
                width: parent.width; from: 0; to: 1; stepSize: .01
                first.value: panel.current.shape?.low ?? .25; second.value: panel.current.shape?.high ?? .75
                first.onMoved: panel.model.adjust("low",first.value)
                second.onMoved: panel.model.adjust("high",second.value)
                first.onPressedChanged: { if(first.pressed) panel.raw.beginGesture(); else panel.raw.endGesture() }
                second.onPressedChanged: { if(second.pressed) panel.raw.beginGesture(); else panel.raw.endGesture() }
                background: Rectangle { y: luminance.height/2-4; width: parent.width; height: 8; radius: 3; gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: "#101112" } GradientStop { position: 1; color: "white" } } }
            }
            Field { label: "Smoothness"; key: "smooth"; low: 0; high: 100 }
            Action { text: "Sample on photo"; onClicked: panel.model.sampleAgain() }
            Copy { width: parent.width; text: "Click to sample a tone, or drag to sample a range of tones." }
        }
        Copy { visible: panel.model.tool === "linear" || panel.model.tool === "radial" || panel.model.tool === "object"; width: parent.width; text: panel.model.tool === "object" ? "Click to select. Ctrl-click excludes an area." : "Drag on the photo to define the mask." }
        Action { visible: !!panel.model.tool; text: "Finish drawing"; onClicked: panel.model.stop() }
        Rectangle { width: parent.width; height: 1; color: theme.line }
        Copy { visible: panel.model.selected < 0; text: "No mask selected"; color: theme.fg3 }
        Column {
            visible: panel.model.selected >= 0; width: parent.width; spacing: 8
            TextField { width: parent.width; text: panel.current.name || "Mask"; color: theme.fg; selectByMouse: true; maximumLength: 64; onEditingFinished: panel.model.rename(text); background: Rectangle { radius: 6; color: theme.base; border.color: theme.line } }
            Field { label: "Amount"; key: "amount"; low: 0; high: 100 }
            Field { visible: panel.shapeKind === "radial"; label: "Feather"; key: "feather"; low: 0; high: 100 }
            Action { text: "Reset adjustments"; onClicked: panel.model.resetAdjustments() }
            Column {
                x: -13; width: parent.width+26
                Repeater {
                    model: ["Light","Color","Curve","Color Mixer","Color Grading","Effects","Calibration"]
                    RawAdjustmentSection {
                        id: localSection
                        required property string modelData
                        objectName: "maskSection_"+modelData
                        title: modelData; expanded: title==="Light" || title==="Color"
                        Loader {
                            width: parent.width; active: localSection.expanded
                            sourceComponent: localSection.title==="Curve" ? curvePage : localSection.title==="Color Mixer" ? mixerPage : localSection.title==="Color Grading" ? gradingPage : localSection.title==="Calibration" ? calibrationPage : basicPage
                            Component { id: basicPage; RawBasicAdjustments { model: localModel; section: localSection.title; localAdjustments: true } }
                            Component { id: curvePage; RawCurve { model: localModel } }
                            Component { id: mixerPage; RawColorMixer { model: localModel } }
                            Component { id: gradingPage; RawColorGrading { model: localModel } }
                            Component { id: calibrationPage; RawTechnicalAdjustments { model: localModel; section: "Calibration" } }
                        }
                    }
                }
            }
        }
    }
}
