import QtQuick

// The launch card: the splash artwork with the status line, the progress bar and the version drawn over it. The positions are the Widgets
// splash's (ui/splash_screen.py paintEvent), which scales everything from a 1633 px wide design.
Item {
    id: card
    property string status: "Starting…"
    property real progress: 8              // 0..100
    property string version: ""
    readonly property real s: width / 1633
    readonly property real pad: 115 * s
    readonly property real barH: Math.max(8, 25 * s)
    Image { anchors.fill: parent; source: Qt.resolvedUrl("../../ui/assets/splash_background-v8.png"); fillMode: Image.Stretch; smooth: true; mipmap: true }
    Text {
        x: card.pad; y: 805 * card.s; width: card.width - 2 * card.pad; height: 34 * card.s
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
        text: card.status; color: "#b9c9e2"; font.pixelSize: Math.max(12, Math.round(25 * card.s))
    }
    Rectangle {
        id: track
        x: card.pad; y: 849 * card.s; width: card.width - 2 * card.pad; height: card.barH; radius: height / 2
        color: Qt.rgba(20 / 255, 25 / 255, 42 / 255, 224 / 255); border.color: "#3e475f"; border.width: Math.max(1, 1.5 * card.s)
        Rectangle {
            readonly property real inset: Math.max(2, 3 * card.s)
            x: inset; y: inset; height: parent.height - 2 * inset; radius: height / 2
            width: Math.min(parent.width - 2 * inset, Math.max(height, (parent.width - 2 * inset) * card.progress / 100)); visible: card.progress > 0
            gradient: Gradient { orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "#35e7dd" } GradientStop { position: 0.55; color: "#66dffc" } GradientStop { position: 1.0; color: "#a06df5" } }
        }
    }
    Text {
        visible: card.version !== ""
        readonly property real corner: Math.max(14, 28 * card.s)
        readonly property real vh: Math.max(18, 30 * card.s)
        x: card.width * 0.72; y: card.height - corner - vh; width: card.width * 0.28 - corner; height: vh
        horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter
        text: "v" + card.version; color: "#9caac2"; font.pixelSize: Math.max(11, Math.round(20 * card.s))
    }
}
