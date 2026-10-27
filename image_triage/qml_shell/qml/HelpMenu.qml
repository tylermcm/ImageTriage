import QtQuick
import QtQuick.Window

// The Help menu under the top bar's Help button: Help, Keyboard shortcuts, Browse Library, File associations, updates and About.
// The model is reference_model.py (`reference`); this file draws its `view.menuItems` (mockup: scripts/qml_parity/help.dc.html).
Item {
    id: root
    property real menuX: 205
    readonly property var v: reference.view
    anchors.fill: parent
    visible: v.menu
    z: 100
    MouseArea { x: 0; y: 42; width: parent.width; height: parent.height - 42; acceptedButtons: Qt.AllButtons; hoverEnabled: true; onWheel: (w) => { w.accepted = true }; onClicked: reference.closeMenu() }   // below the bar, so the bar's titles stay live
    Item {
        id: card
        x: root.menuX; y: 41; width: 268; height: col.height + 12
        opacity: root.v.menu ? 1 : 0; scale: root.v.menu ? 1 : 0.96; transformOrigin: Item.TopLeft
        Behavior on scale { NumberAnimation { duration: 280; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }
        Behavior on opacity { NumberAnimation { duration: 160 } }
        Glass { anchors.fill: parent; radius: 14; frosted: false; tint: theme.s2; shadowAlphaA: 0.7; shadowAlphaB: 0; shadowOffsetA: 24; shadowInset: 16 }
        Rectangle { anchors.fill: parent; radius: 14; color: "transparent"; border.color: Qt.alpha(theme.lift, 0.11) }
        Column {
            id: col
            x: 6; y: 6; width: parent.width - 12
            Repeater {
                model: root.v.menuItems
                delegate: Item {
                    id: row
                    required property var modelData
                    width: col.width; height: modelData.sep ? 13 : 34
                    Rectangle { visible: row.modelData.sep; x: 4; y: 6; width: parent.width - 8; height: 1; color: theme.lineSoft }
                    Rectangle { visible: !row.modelData.sep; anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, rh.hovered ? 0.08 : 0) }
                    Icon { visible: !row.modelData.sep; x: 10; anchors.verticalCenter: parent.verticalCenter; path: row.modelData.d || ""; size: 16; strokeWidth: 1.7; color: rh.hovered ? theme.fg : theme.fg2 }
                    Text { visible: !row.modelData.sep; x: 36; anchors.verticalCenter: parent.verticalCenter; text: row.modelData.label || ""; color: rh.hovered ? theme.fg : theme.fg2; font.pixelSize: 13 }
                    Text { visible: !row.modelData.sep && (row.modelData.kbd || "") !== ""; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
                           text: row.modelData.kbd || ""; color: theme.fg3; font.pointSize: UiState.pt(10); font.weight: Font.Medium; leftPadding: 6; rightPadding: 6; topPadding: 1.5; bottomPadding: 1.5
                           Rectangle { anchors.fill: parent; z: -1; radius: 5; color: "transparent"; border.color: theme.line }
                           Rectangle { x: 1; y: parent.height - 2; width: parent.width - 2; height: 1.5; radius: 1; color: theme.line } }
                    HoverHandler { id: rh; enabled: !row.modelData.sep; cursorShape: Qt.PointingHandCursor }
                    TapHandler { enabled: !row.modelData.sep; onTapped: reference.open(row.modelData.id) }
                    Accessible.role: row.modelData.sep ? Accessible.Separator : Accessible.MenuItem; Accessible.name: row.modelData.label || ""
                }
            }
        }
    }
    Keys.onEscapePressed: reference.closeMenu()
}
