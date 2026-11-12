import QtQuick
import QtQuick.Controls

Column {
    id: panel
    required property var model
    required property var raw
    spacing: 10
    enabled: raw.loaded && !raw.saving && !raw.busy
    Row {
        visible: panel.raw.busy; width: parent.width; spacing: 8
        BusyIndicator { width: 24; height: 24; running: visible }
        Text { text: "Applying retouch…"; color: theme.fg2; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
    }
    Text { text: "Remove"; color: theme.fg; font.pixelSize: 14; font.bold: true }
    Grid {
        width: parent.width; columns: 2; spacing: 6
        Repeater {
            model: [{name:"Remove",kind:"remove"},{name:"Spot Healing",kind:"spot"},{name:"Heal",kind:"heal"},{name:"Clone",kind:"clone"}]
            SButton { required property var modelData; objectName: "retouchTool_"+modelData.kind; width: (parent.width-6)/2; text: modelData.name; variant: panel.model.kind===modelData.kind ? "primary" : "normal"; onClicked: panel.model.choose(modelData.kind) }
        }
    }
    Repeater {
        model: [{label:"Size",key:"size",low:.1,high:50,step:.1},{label:"Feather",key:"feather",low:0,high:100,step:1},{label:"Opacity",key:"opacity",low:0,high:100,step:1}]
        RawColorSlider {
            required property var modelData
            objectName: "retouchOption_"+modelData.key
            width: parent.width; controlHeight: 32
            label: modelData.label; from: modelData.low; to: modelData.high; step: modelData.step; decimals: modelData.key==="size" ? 1 : 0
            value: panel.model[modelData.key]
            onCommitted: (next) => panel.model.option(modelData.key,next)
        }
    }
    Text { width: parent.width; text: panel.model.kind==="clone" || panel.model.kind==="heal" ? (panel.model.source.length ? "Source selected. Paint to apply. Alt-click to choose another source." : "Alt-click the photo to choose a source, then paint over the area to replace.") : "Paint over the area to remove. Each stroke is one undo step."; color: theme.fg2; font.pixelSize: 12; wrapMode: Text.WordWrap }
    Rectangle { width: parent.width; height: 1; color: theme.line }
    Text { text: panel.model.rows.length+" retouch strokes"; color: theme.fg2; font.pixelSize: 12 }
    Row {
        spacing: 6
        SButton { text: "Undo"; enabled: panel.model.rows.length>0; onClicked: panel.raw.undo() }
        SButton { text: "Clear strokes"; enabled: panel.model.rows.length>0; onClicked: panel.model.clear() }
    }
    Repeater {
        model: panel.model.rows
        Row {
            required property var modelData
            required property int index
            width: parent.width
            Text { width: parent.width-62; height: 28; verticalAlignment: Text.AlignVCenter; text: (index+1)+" · "+({remove:"Remove",spot:"Spot Healing",heal:"Heal",clone:"Clone"}[modelData.kind]); color: theme.fg2; font.pixelSize: 12 }
            SButton { text: "Delete"; h: 28; onClicked: panel.model.remove(parent.index) }
        }
    }
    Text { width: parent.width; text: "Generative AI and automatic distraction removal are not available."; color: theme.fg3; font.pixelSize: 11; wrapMode: Text.WordWrap }
}
