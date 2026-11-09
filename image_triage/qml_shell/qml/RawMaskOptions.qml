import QtQuick
import QtQuick.Controls

Item {
    id: dialog
    required property var model
    objectName: "maskOptionsPane"
    implicitHeight: content.implicitHeight
    visible: model.optionsOpen
    property var selectedIndices: []
    onVisibleChanged: if (visible) selectedIndices=[]
    Column {
        id: content; width: parent.width; spacing: 12
        Text { text: dialog.model.optionsTitle; color: theme.fg; font.pixelSize: 15; font.bold: true }
        Text { width: parent.width; text: dialog.model.optionsTitle==="Select a person" ? "Choose the person you want to adjust." : "Select the regions to include in this mask."; color: theme.fg2; font.pixelSize: 12; wrapMode: Text.WordWrap }
        ScrollView {
          width: parent.width; height: Math.min(280,choices.implicitHeight)
          clip: true; contentWidth: availableWidth
          ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
          Column { id: choices; width: parent.width
          Repeater {
            model: dialog.model.choices
            CheckBox {
                required property var modelData
                required property int index
                objectName: "aiMaskChoice_"+index
                id: choice
                width: choices.width; height: 34; text: modelData.label
                indicator: Rectangle { x: 0; y: 9; width: 16; height: 16; radius: 4; color: choice.checked ? theme.accent : theme.base; border.color: choice.checked ? theme.accent : theme.lineSoft
                    Text { anchors.centerIn: parent; text: "✓"; color: theme.onAccent; visible: choice.checked; font.pixelSize: 12 }
                }
                contentItem: Text { leftPadding: 25; text: choice.text; color: theme.fg; verticalAlignment: Text.AlignVCenter; font.pixelSize: 13; elide: Text.ElideRight }
                checked: dialog.selectedIndices.indexOf(index)>=0
                onClicked: {
                    if(dialog.model.optionsTitle==="Select a person")dialog.selectedIndices=[index]
                    else {
                        var picks=dialog.selectedIndices.slice();var at=picks.indexOf(index)
                        if(at<0)picks.push(index);else picks.splice(at,1)
                        dialog.selectedIndices=picks
                    }
                }
            }
          }
          }
        }
        Row {
            anchors.right: parent.right; spacing: 8
            SButton { text: "Cancel"; onClicked: dialog.model.closeOptions() }
            SButton { objectName: "createMaskSelection"; text: "Create mask"; enabled: dialog.selectedIndices.length>0; variant: "primary"; onClicked: dialog.model.createSelection(dialog.selectedIndices) }
        }
        Rectangle { width: parent.width; height: 1; color: theme.line }
    }
}
