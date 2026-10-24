import QtQuick
import QtQuick.Effects

// A segmented control whose highlight slides (with a little spring) to the chosen segment.
Item {
    id: seg
    property var labels: []              // text per segment (empty string for an icon segment)
    property var icons: []               // icon name per segment (optional)
    property var extras: []              // small trailing text per segment (counts)
    property int current: 0
    property int padH: 12
    property bool showExtras: true
    property bool equal: true           // grid-auto-columns: 1fr, every segment as wide as the widest
    property bool fill: false           // stretch the segments to share the whole width equally
    property real maxCell: 0
    signal activated(int index)
    implicitHeight: 36
    implicitWidth: row.implicitWidth + 4

    Rectangle { anchors.fill: parent; radius: 14; color: Qt.alpha(theme.base, 0.6) }
    Item {
        id: thumb
        y: 2; height: seg.height - 4
        property Item target: { void row.width; void rep.count; return rep.itemAt(seg.current); }
        x: target ? target.x + 2 : 2
        width: target ? target.width : 40
        Behavior on x { SpringAnimation { spring: 4.5; damping: 0.30; epsilon: 0.25 } }
        Behavior on width { SpringAnimation { spring: 4.5; damping: 0.30; epsilon: 0.25 } }

        // box-shadow 0 4px 10px -4px rgba(0,0,0,.5): a smaller copy, lowered and blurred, under the pill
        Rectangle {
            x: 4; y: 4 + 4; width: parent.width - 8; height: parent.height - 8; radius: 8
            color: "black"; opacity: 0.5
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 14; autoPaddingEnabled: true }
        }
        Rectangle { anchors.fill: parent; radius: 12; color: theme.s3 }
        // inset 0 1px 0 lift 10%: a one pixel highlight along the top edge that follows the rounded corners
        Item {
            anchors.fill: parent
            layer.enabled: true
            layer.effect: MultiEffect { maskEnabled: true; maskSource: capMask; maskThresholdMin: 0.5; maskSpreadAtMin: 1.0 }
            Rectangle { anchors.fill: parent; radius: 12; color: "transparent"; border.color: Qt.alpha(theme.lift, 0.10); border.width: 1 }
            Rectangle { y: 1; x: 0; width: parent.width; height: parent.height - 1; radius: 12; color: theme.s3 }   // hides the lower part of the ring
        }
        Item { id: capMask; anchors.fill: parent; visible: false; layer.enabled: true
            Rectangle { anchors.fill: parent; radius: 12 } }
    }
    Row {
        id: row
        x: 2; y: 2
        Repeater {
            id: rep
            model: Math.max(seg.labels.length, seg.icons.length)
            delegate: Item {
                id: cell
                required property int index
                readonly property string text: seg.labels[index] || ""
                readonly property string icon: seg.icons[index] || ""
                readonly property string extra: seg.showExtras ? (seg.extras[index] || "") : ""
                height: seg.height - 4
                Component.onCompleted: seg.maxCell = Math.max(seg.maxCell, content.implicitWidth + seg.padH * 2)
                Connections { target: content; function onImplicitWidthChanged() { seg.maxCell = Math.max(seg.maxCell, content.implicitWidth + seg.padH * 2) } }
                width: seg.fill ? (seg.width - 4) / Math.max(1, rep.count) : (seg.equal ? seg.maxCell : content.implicitWidth + seg.padH * 2)
                Row {
                    id: content
                    anchors.centerIn: parent
                    spacing: 5
                    Icon { visible: cell.icon !== ""; name: cell.icon; size: 16; color: seg.current === cell.index ? theme.fg : theme.fg2; anchors.verticalCenter: parent.verticalCenter }
                    Text { visible: cell.text !== ""; text: cell.text; font.pixelSize: 13; color: seg.current === cell.index ? theme.fg : theme.fg2; anchors.verticalCenter: parent.verticalCenter }
                    Text { visible: cell.extra !== ""; text: cell.extra; font.pixelSize: 11; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: seg.activated(cell.index) }
                Accessible.role: Accessible.Button
                Accessible.name: cell.text !== "" ? cell.text : cell.icon
            }
        }
    }
}
