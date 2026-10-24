import QtQuick
import QtQuick.Controls
import QtQuick.Shapes

// The photo grid: a virtualized GridView over the model, thumbnails from the disk cache (ThumbImage), edge-to-edge with a 2 px gutter.
Item {
    id: root
    property alias view: gv
    readonly property int minCell: UiState.dens === "s" ? 150 : UiState.dens === "m" ? 230 : 340
    readonly property int cols: Math.max(1, Math.floor(gv.width / minCell))
    property int bottomSpace: 132
    property Item overlay                // something floating over the grid (the bar): taps inside it are not the grid's
    signal openRequested(int row)

    function blocked(scenePoint) {
        if (!overlay) return false;
        var p = overlay.mapFromItem(null, scenePoint.x, scenePoint.y);
        return p.x >= 0 && p.y >= 0 && p.x <= overlay.width && p.y <= overlay.height;
    }

    function bucket(px) {
        var sizes = [160, 240, 360, 540, 800];
        for (var i = 0; i < sizes.length; ++i) if (px <= sizes[i]) return sizes[i];
        return 800;
    }

    GridView {
        id: gv
        objectName: "photoGrid"
        anchors.fill: parent
        anchors.margins: 1
        clip: true
        focus: true
        keyNavigationEnabled: true
        boundsBehavior: Flickable.StopAtBounds
        model: photos
        cellWidth: width / root.cols
        cellHeight: (cellWidth - 2) * 2 / 3 + 2   // a 3:2 card plus the 2px gutter
        cacheBuffer: cellHeight * 3
        bottomMargin: root.bottomSpace
        reuseItems: true
        highlightFollowsCurrentItem: false
        currentIndex: 0
        ScrollBar.vertical: ScrollBar {
            policy: gv.contentHeight + gv.bottomMargin > gv.height + 1 ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
            contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.28) }
            background: Item {}
        }
        Keys.onPressed: (e) => {
            var k = e.key;
            if (k === Qt.Key_W) photos.setDecision(currentIndex, "win");
            else if (k === Qt.Key_K) photos.setDecision(currentIndex, "keep");
            else if (k === Qt.Key_X) photos.setDecision(currentIndex, "rej");
            else if (k === Qt.Key_U) photos.setDecision(currentIndex, "");
            else if (k >= Qt.Key_1 && k <= Qt.Key_5) photos.setRating(currentIndex, k - Qt.Key_0);
            else if (k === Qt.Key_I) UiState.insp = !UiState.insp;
            else if (k === Qt.Key_Return || k === Qt.Key_Space) root.openRequested(currentIndex);
            else return;
            e.accepted = true;
        }

        delegate: Item {
            id: cell
            GridView.onPooled: img.release()
            GridView.onReused: img.rev++
            required property int index
            required property string path
            required property string name
            required property string ext
            required property bool isRaw
            required property string decision
            required property int rating
            required property int ai
            width: GridView.view.cellWidth
            height: GridView.view.cellHeight
            readonly property bool current: GridView.isCurrentItem
            readonly property bool hot: hover.hovered || current

            Item {
                anchors.fill: parent
                anchors.margins: 1
                clip: true
                Rectangle { anchors.fill: parent; color: theme.s2 }
                ThumbImage {
                    id: img
                    anchors.fill: parent
                    path: cell.path
                    bucket: root.bucket(Math.ceil(cell.width * Screen.devicePixelRatio))
                    imageOpacity: cell.decision === "rej" ? 0.38 : 1
                    desaturate: cell.decision === "rej" ? 0.75 : 0
                    scale: hover.hovered ? 1.04 : 1
                    Behavior on scale { NumberAnimation { duration: 500; easing.type: Easing.OutCubic } }
                }
                Rectangle {
                    // the name / stars / format strip: padding 22 9 7, 11.5px text, a 0.72 black ramp
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    height: 45.1
                    opacity: cell.hot ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 200 } }
                    gradient: Gradient { GradientStop { position: 0; color: "transparent" } GradientStop { position: 1; color: "#b8000000" } }
                    Text {
                        visible: UiState.showNames
                        anchors.left: parent.left; anchors.leftMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7
                        width: parent.width - 120; elide: Text.ElideRight; height: 16.1; verticalAlignment: Text.AlignVCenter
                        text: cell.name; color: "white"; font.pointSize: UiState.pt(11.5)
                    }
                    Row {
                        anchors.right: parent.right; anchors.rightMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7; spacing: 7
                        Item {
                            width: 62.5; height: 13
                            Text { y: 1; text: "\u2605\u2605\u2605\u2605\u2605"; color: Qt.rgba(1, 1, 1, 0.28); font.pixelSize: 11; font.letterSpacing: 1; height: 11 }
                            Item {
                                y: 1; width: parent.width * cell.rating / 5; height: 11; clip: true
                                Text { text: "\u2605\u2605\u2605\u2605\u2605"; color: theme.star; font.pixelSize: 11; font.letterSpacing: 1; height: 11 }
                            }
                        }
                        Rectangle {
                            visible: UiState.showBadges
                            width: fmt.implicitWidth + 11.7; height: 13; radius: 5; color: cell.isRaw ? Qt.alpha(theme.accent, 0.7) : Qt.rgba(1, 1, 1, 0.18)
                            Text { id: fmt; anchors.centerIn: parent; text: cell.ext; color: "white"; font.pointSize: UiState.pt(9.5); font.weight: Font.DemiBold; font.letterSpacing: 0.38 }
                        }
                    }
                }
                Item {
                    // decision badge: a 22px disc with a 3px 40% black ring outside it (box-shadow), glyph drawn like the mockup's CSS
                    id: badge
                    visible: UiState.showBadges && cell.decision !== ""
                    x: parent.width - 8 - 22; y: 8; width: 22; height: 22
                    readonly property color tone: cell.decision === "win" ? theme.win : cell.decision === "keep" ? theme.keep : cell.decision === "rej" ? theme.rej : theme.rev
                    Rectangle { x: -3; y: -3; width: 28; height: 28; radius: 14; color: Qt.rgba(0, 0, 0, 0.4) }
                    Rectangle { anchors.fill: parent; radius: 11; color: badge.tone }
                    Shape {
                        visible: cell.decision === "win"
                        x: 5.5; y: 5.5; width: 11; height: 11; preferredRendererType: Shape.CurveRenderer
                        ShapePath {
                            strokeColor: "transparent"; fillColor: "#10261b"
                            PathPolyline { path: [Qt.point(5.5, 0), Qt.point(6.82, 3.96), Qt.point(11, 4.18), Qt.point(7.7, 6.6), Qt.point(8.8, 11), Qt.point(5.5, 8.36), Qt.point(2.2, 11), Qt.point(3.3, 6.6), Qt.point(0, 4.18), Qt.point(4.18, 3.96)] }
                        }
                    }
                    Item {
                        visible: cell.decision === "keep"
                        x: 7.5; y: 4; width: 7; height: 12; rotation: 45; transformOrigin: Item.Center   // ::before is content-box: 5x10 plus 2px borders
                        Rectangle { x: 5; width: 2; height: 12; color: "white" }
                        Rectangle { y: 10; width: 7; height: 2; color: "white" }
                    }
                    Item {
                        visible: cell.decision === "rej"
                        anchors.fill: parent
                        Rectangle { anchors.centerIn: parent; width: 10; height: 2; radius: 1; rotation: 45; color: "white" }
                        Rectangle { anchors.centerIn: parent; width: 10; height: 2; radius: 1; rotation: -45; color: "white" }
                    }
                    Rectangle { visible: cell.decision === "rev"; anchors.centerIn: parent; width: 12; height: 12; radius: 6; color: "transparent"; border.width: 2; border.color: "#2a1f06" }
                }
                Item {
                    // selection: 3px accent ring with a 1px 45% black line inside it, like the two inset shadows
                    anchors.fill: parent
                    opacity: cell.current ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 200 } }
                    Rectangle { anchors.fill: parent; color: "transparent"; radius: 3; border.color: theme.accent; border.width: 3 }
                    Rectangle { anchors.fill: parent; anchors.margins: 3; color: "transparent"; border.color: Qt.rgba(0, 0, 0, 0.45); border.width: 1 }
                }
            }
            HoverHandler { id: hover }
            TapHandler {
                onTapped: (point) => { if (root.blocked(point.scenePosition)) return; gv.currentIndex = cell.index; gv.forceActiveFocus() }
                onDoubleTapped: (point) => { if (!root.blocked(point.scenePosition)) root.openRequested(cell.index) }
            }
        }
    }
}
