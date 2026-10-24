import QtQuick
import QtQuick.Controls

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
        cellHeight: Math.round(cellWidth * 2 / 3)
        cacheBuffer: cellHeight * 3
        bottomMargin: root.bottomSpace
        reuseItems: true
        highlightFollowsCurrentItem: false
        currentIndex: 0
        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
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
                    scale: hover.hovered ? 1.04 : 1
                    Behavior on scale { NumberAnimation { duration: 500; easing.type: Easing.OutCubic } }
                }
                Rectangle {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    height: 46
                    opacity: cell.hot ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 180 } }
                    gradient: Gradient { GradientStop { position: 0; color: "transparent" } GradientStop { position: 1; color: "#b8000000" } }
                    Text {
                        visible: UiState.showNames
                        anchors.left: parent.left; anchors.leftMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7
                        width: parent.width - 100; elide: Text.ElideRight
                        text: cell.name; color: "white"; font.pixelSize: 12
                    }
                    Row {
                        anchors.right: parent.right; anchors.rightMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7; spacing: 7
                        Item {
                            width: 56; height: 12
                            Text { text: "★★★★★"; color: Qt.rgba(1, 1, 1, 0.28); font.pixelSize: 11; font.letterSpacing: 1 }
                            Item {
                                width: parent.width * cell.rating / 5; height: parent.height; clip: true
                                Text { text: "★★★★★"; color: theme.star; font.pixelSize: 11; font.letterSpacing: 1 }
                            }
                        }
                        Rectangle {
                            visible: UiState.showBadges
                            width: fmt.implicitWidth + 10; height: 15; radius: 5; color: cell.isRaw ? Qt.alpha(theme.accent, 0.78) : Qt.rgba(1, 1, 1, 0.18)
                            Text { id: fmt; anchors.centerIn: parent; text: cell.ext; color: "white"; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 0.4 }
                        }
                    }
                }
                Rectangle {
                    visible: UiState.showBadges && cell.decision !== ""
                    anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                    width: 22; height: 22; radius: 11
                    color: cell.decision === "win" ? theme.win : cell.decision === "keep" ? theme.keep : cell.decision === "rej" ? theme.rej : theme.rev
                    border.color: Qt.rgba(0, 0, 0, 0.4); border.width: 3
                    Icon {
                        anchors.centerIn: parent; size: 12; strokeWidth: 2.2
                        name: cell.decision === "win" ? "star" : cell.decision === "keep" ? "check" : cell.decision === "rej" ? "x" : "eye"
                        color: cell.decision === "win" ? "#10261b" : "white"; filled: cell.decision === "win"
                    }
                }
                Rectangle {
                    anchors.fill: parent; color: "transparent"; radius: 3
                    border.color: theme.accent; border.width: cell.current ? 3 : 0
                    Behavior on border.width { NumberAnimation { duration: 120 } }
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
