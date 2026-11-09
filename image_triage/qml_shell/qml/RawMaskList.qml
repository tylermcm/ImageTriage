import QtQuick
import QtQuick.Controls

Rectangle {
    id: panel
    required property var model
    required property var raw
    property bool overlayVisible: true
    property real maxBodyHeight: 310
    property string combineOp: "add"
    signal overlayToggled(bool value)
    color: theme.s1; radius: 10; border.color: theme.line
    implicitHeight: body.implicitHeight+18; height: implicitHeight
    opacity: model.autoHide && model.pointerOnImage && !!model.tool ? 0 : 1
    enabled: opacity>0
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: (event) => {event.accepted=true} }
    component Choice: MenuItem {
        id: choice
        height: 30
        leftPadding: 10; rightPadding: 10
        indicator: null
        contentItem: Text { text: (choice.checkable && choice.checked ? "✓  " : "")+choice.text; color: choice.enabled ? theme.fg : theme.fg3; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 5; color: choice.highlighted ? theme.s3 : "transparent" }
    }
    component DarkMenu: Menu {
        modal: true; dim: false; width: 225
        background: Rectangle { radius: 8; color: theme.s1; border.color: theme.line }
    }
    function combine(op) { combineOp=op; tools.open() }
    Column {
        id: body; x: 9; y: 9; width: parent.width-18; spacing: 8
        Row {
            width: parent.width; spacing: 4
            SButton { objectName: "newFloatingMask"; text: "+  Create New Mask"; width: parent.width-58; hpad: 4; h: 28; variant: "ghost"; enabled: !panel.raw.saving && !panel.model.ai.busy; onClicked: panel.model.createNew() }
            SButton { text: "↶"; width: 25; h: 28; variant: "ghost"; enabled: panel.model.rows.length>0; onClicked: panel.model.resetAdjustments() }
            SButton { text: "◉"; width: 25; h: 28; variant: "ghost"; enabled: panel.model.selected>=0; onClicked: panel.model.toggle("enabled") }
        }
        Rectangle { width: parent.width; height: 1; color: theme.line }
        ScrollView {
            objectName: "floatingMaskScroll"
            width: parent.width; height: Math.min(panel.maxBodyHeight,items.implicitHeight)
            clip: true; contentWidth: availableWidth; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            Column {
                id: items; width: parent.width; spacing: 4
                Repeater {
                    model: panel.model.rows
                    Column {
                        required property var modelData
                        required property int index
                        width: items.width; spacing: 4
                        Rectangle {
                            objectName: "maskRow_"+index
                            width: parent.width; height: 55; radius: 7
                            color: panel.model.selected===index ? theme.s3 : "transparent"
                            border.color: panel.model.selected===index ? theme.accent : "transparent"
                            Rectangle {
                                x: 6; y: 6; width: 44; height: 42; radius: 4; color: "#08090a"; border.color: theme.line
                                Image { anchors.fill: parent; anchors.margins: 2; source: panel.model.thumbnail(index); fillMode: Image.PreserveAspectFit; asynchronous: true }
                            }
                            Text { x: 57; width: parent.width-x-28; anchors.verticalCenter: parent.verticalCenter; text: (modelData.enabled===false ? "○  " : "")+(modelData.name || "Mask "+(index+1)); color: theme.fg; font.pixelSize: 12; elide: Text.ElideRight }
                            MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton; onClicked: (event) => {panel.model.select(index);if(event.button===Qt.RightButton)actions.popup()} }
                            SButton { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 26; h: 28; text: "⋯"; variant: "ghost"; onClicked: {panel.model.select(index);actions.popup()} }
                        }
                        Column {
                            visible: panel.model.selected===index
                            width: parent.width; spacing: 3
                            Repeater {
                                model: panel.model.selected===index ? panel.model.components : []
                                Rectangle {
                                    id: componentRow
                                    required property var modelData
                                    required property int index
                                    x: 12; width: parent.width-12; height: 28; radius: 5
                                    color: panel.model.component===index ? theme.s3 : "transparent"
                                    Text { x: 7; width: parent.width-35; anchors.verticalCenter: parent.verticalCenter; text: (componentRow.modelData.op==="subtract" ? "− " : componentRow.modelData.op==="intersect" ? "∩ " : "+ ")+(componentRow.modelData.invert ? "Inverted " : "")+componentRow.modelData.label; color: theme.fg2; font.pixelSize: 11; elide: Text.ElideRight }
                                    MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton; onClicked: (event) => {panel.model.selectComponent(componentRow.index);if(event.button===Qt.RightButton)componentActions.popup()} }
                                    SButton { anchors.right: parent.right; width: 26; h: 28; text: "⋯"; variant: "ghost"; onClicked: {panel.model.selectComponent(componentRow.index);componentActions.popup()} }
                                }
                            }
                            Row {
                                spacing: 4; width: parent.width
                                Repeater {
                                    model: ["Add","Subtract","Intersect"]
                                    SButton { required property string modelData; width: (parent.width-8)/3; h: 27; hpad: 2; text: modelData; enabled: !panel.raw.saving && !panel.model.ai.busy; onClicked: panel.combine(modelData.toLowerCase()) }
                                }
                            }
                        }
                    }
                }
            }
        }
        Rectangle { width: parent.width; height: 1; color: theme.line }
        Row {
            width: parent.width
            CheckBox { id: overlayCheck; text: panel.model.autoOverlay ? "Show Overlay (auto)" : "Show Overlay"; width: parent.width-57; height: 30; checked: panel.overlayVisible; onToggled: panel.overlayToggled(checked)
                indicator: Rectangle { x: 0; y: 7; width: 14; height: 14; radius: 3; color: overlayCheck.checked ? theme.accent : theme.base; border.color: theme.lineSoft
                    Text { anchors.centerIn: parent; text: "✓"; color: theme.onAccent; visible: overlayCheck.checked; font.pixelSize: 11 }
                }
                contentItem: Text { text: overlayCheck.text; leftPadding: 20; color: theme.fg2; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter }
            }
            Rectangle { width: 22; height: 30; color: "transparent"
                Rectangle { anchors.centerIn: parent; width: 14; height: 14; radius: 7; color: panel.model.overlayColor; border.color: theme.fg2 }
                TapHandler { onTapped: colorSettings.open() }
            }
            SButton { objectName: "maskOverlayMenu"; width: 32; h: 30; text: "⋯"; variant: "ghost"; onClicked: overlayOptions.open() }
        }
    }
    DarkMenu {
        id: actions; objectName: "floatingMaskActions"
        Choice { text: "Rename…"; onTriggered: {renameField.text=panel.model.current.name;renameDialog.open()} }
        Choice { text: "Invert Mask"; enabled: !(panel.model.current.parts?.length); onTriggered: panel.model.toggle("invert"); ToolTip.text: "For combined masks, invert individual components."; ToolTip.visible: hovered && !enabled }
        Choice { text: "Duplicate and Invert Mask"; enabled: panel.model.rows.length<16 && !(panel.model.current.parts?.length); onTriggered: panel.model.duplicateMask(true) }
        Choice { text: "Intersect Mask With…"; onTriggered: panel.combine("intersect") }
        MenuSeparator {}
        Choice { text: "Duplicate Mask"; enabled: panel.model.rows.length<16; onTriggered: panel.model.duplicateMask(false) }
        Choice { text: panel.model.current.enabled===false ? "Show Mask" : "Hide Mask"; onTriggered: panel.model.toggle("enabled") }
        Choice { text: "Delete Mask"; onTriggered: panel.model.remove() }
        Choice { text: "Reset Mask Adjustments"; onTriggered: panel.model.resetAdjustments() }
        MenuSeparator {}
        Choice { text: "Update AI Masks"; enabled: false }
        Choice { text: "Delete Empty Masks"; onTriggered: panel.model.removeEmpty() }
        Choice { text: "Delete All Masks"; onTriggered: panel.model.removeAll() }
    }
    DarkMenu {
        id: componentActions; objectName: "floatingComponentActions"
        Choice { text: "Rename Component…"; enabled: false }
        Choice { text: "Invert Component"; onTriggered: panel.model.componentAction("invert") }
        Choice { text: "Convert to Add"; enabled: panel.model.component>0; onTriggered: panel.model.componentAction("add") }
        Choice { text: "Convert to Subtract"; enabled: panel.model.component>0; onTriggered: panel.model.componentAction("subtract") }
        Choice { text: "Convert to Intersect"; enabled: panel.model.component>0; onTriggered: panel.model.componentAction("intersect") }
        MenuSeparator {}
        Choice { text: "Duplicate Component"; enabled: panel.model.components.length<9; onTriggered: panel.model.componentAction("duplicate") }
        Choice { text: "Hide Component"; enabled: false }
        Choice { text: "Delete Component"; onTriggered: panel.model.componentAction("delete") }
        Choice { text: "Move Up"; enabled: panel.model.component>1; onTriggered: panel.model.componentAction("up") }
        Choice { text: "Move Down"; enabled: panel.model.component>0 && panel.model.component<panel.model.components.length-1; onTriggered: panel.model.componentAction("down") }
    }
    DarkMenu {
        id: tools; objectName: "floatingMaskTools"
        Repeater {
            model: [{label:"Select Subject",kind:"subject"},{label:"Select Sky",kind:"sky"},{label:"Select Background",kind:"background"},{label:"Select Landscape…",kind:"scene"},{label:"Select Objects",kind:"object"},{label:"Select People…",kind:"people"},{label:"Brush",kind:"brush"},{label:"Linear Gradient",kind:"linear"},{label:"Radial Gradient",kind:"radial"},{label:"Color Range",kind:"colorRange"},{label:"Luminance Range",kind:"luminanceRange"}]
            Choice { required property var modelData; text: modelData.label; enabled: !panel.raw.saving && !panel.model.ai.busy && (panel.model.ai.availability[modelData.kind]===undefined || panel.model.ai.availability[modelData.kind]===""); onTriggered: panel.model.combineWith(panel.combineOp,modelData.kind) }
        }
        Choice { text: "Depth Range"; enabled: false }
    }
    DarkMenu {
        id: overlayOptions; objectName: "floatingOverlayOptions"
        Repeater {
            model: [{label:"Color Overlay",mode:"color"},{label:"Color Overlay on B&W",mode:"color-bw"},{label:"Image on B&W",mode:"image-bw"},{label:"Image on Black",mode:"image-black"},{label:"Image on White",mode:"image-white"},{label:"White on Black",mode:"white-black"}]
            Choice { required property var modelData; text: modelData.label; checkable: true; checked: panel.model.overlayMode===modelData.mode; onTriggered: panel.model.displayOption("mode",modelData.mode) }
        }
        MenuSeparator {}
        Choice { text: "Automatically Toggle Overlay"; checkable: true; checked: panel.model.autoOverlay; onTriggered: panel.model.displayOption("autoOverlay",checked ? "true" : "false") }
        Choice { text: "Auto Hide Masking Panel"; checkable: true; checked: panel.model.autoHide; onTriggered: panel.model.displayOption("autoHide",checked ? "true" : "false") }
        Choice { text: "Show Unselected Mask Pins"; checkable: true; checked: panel.model.unselectedPins; onTriggered: panel.model.displayOption("unselectedPins",checked ? "true" : "false") }
        Choice { text: "Show Pins and Tools"; checkable: true; checked: panel.model.showPins; onTriggered: panel.model.displayOption("showPins",checked ? "true" : "false") }
        MenuSeparator {}
        Choice { text: "Color Overlay Settings…"; onTriggered: colorSettings.open() }
    }
    Popup {
        id: renameDialog; parent: Overlay.overlay; anchors.centerIn: parent; width: 290; padding: 14; modal: true; focus: true
        background: Rectangle { color: theme.s1; radius: 10; border.color: theme.line }
        contentItem: Column { spacing: 12
            Text { text: "Rename Mask"; color: theme.fg; font.pixelSize: 14 }
            TextField { id: renameField; width: parent.width; maximumLength: 64; selectByMouse: true; onAccepted: {panel.model.rename(text);renameDialog.close()} }
            SButton { text: "Save"; variant: "primary"; onClicked: {panel.model.rename(renameField.text);renameDialog.close()} }
        }
    }
    Popup {
        id: colorSettings; parent: Overlay.overlay; anchors.centerIn: parent; width: 290; padding: 14; modal: true
        background: Rectangle { color: theme.s1; radius: 10; border.color: theme.line }
        contentItem: Column { spacing: 12
            Text { text: "Color Overlay Settings"; color: theme.fg; font.pixelSize: 14 }
            TextField { width: parent.width; text: panel.model.overlayColor; color: theme.fg; onEditingFinished: panel.model.displayOption("color",text) }
            Row { spacing: 6
                Repeater { model: ["#ff4040","#55aaff","#50dd88","#ffcc44","#ee66ee"]
                    Rectangle { required property string modelData; width: 34; height: 28; radius: 6; color: modelData; TapHandler { onTapped: panel.model.displayOption("color",parent.modelData) } }
                }
            }
            Text { text: "Opacity · "+panel.model.overlayOpacity+"%"; color: theme.fg2; font.pixelSize: 12 }
            Slider { width: parent.width; from: 0; to: 100; value: panel.model.overlayOpacity; onMoved: panel.model.displayOption("opacity",Math.round(value).toString()) }
            SButton { text: "Done"; onClicked: colorSettings.close() }
        }
    }
}
