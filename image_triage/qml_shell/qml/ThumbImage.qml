import QtQuick
import QtQuick.Effects

// A photo's thumbnail from the disk cache (see thumbs.py). Shows nothing until the file exists; the service says when it does.
Item {
    id: t
    property string path
    property int bucket: 240             // thumbnail width to ask for (a bucket size, so neighbours share cache files)
    property int fillMode: Image.PreserveAspectCrop
    property real imageOpacity: 1
    property bool blurFill: false        // show the whole photo and fill what its shape leaves empty with a blurred copy of it, instead of cropping it to the card
    readonly property int shape: blurFill ? Image.PreserveAspectFit : fillMode
    readonly property bool letterboxed: blurFill && img.status === Image.Ready && width > 0 && (img.paintedWidth < width - 2 || img.paintedHeight < height - 2)
    property real desaturate: 0          // 0..1, like CSS grayscale()
    readonly property int status: img.status
    property int rev: 0
    property string asked: ""
    property url shown                   // the last picture that finished loading: stays up while the next size (or stand-in) loads, so a size change does not flash blank

    function release() { if (asked !== "") { thumbs.unwant(asked); asked = "" } }
    Component.onDestruction: release()
    onPathChanged: { release(); shown = ""; rev++ }

    Image {   // the blurred fill behind a photo that does not match the card's shape
        anchors.fill: parent
        visible: t.letterboxed
        source: t.letterboxed ? t.shown : ""
        sourceSize: Qt.size(96, 64)
        fillMode: Image.PreserveAspectCrop
        opacity: t.imageOpacity * 0.85
        layer.enabled: t.letterboxed
        layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 24; brightness: -0.12; saturation: -t.desaturate }
    }
    Image {
        anchors.fill: parent
        source: t.shown
        asynchronous: true
        sourceSize: Qt.size(t.bucket, Math.round(t.bucket * 2 / 3))
        fillMode: t.shape
        visible: img.status !== Image.Ready && status === Image.Ready
        opacity: t.imageOpacity
        layer.enabled: t.desaturate > 0
        layer.effect: MultiEffect { saturation: -t.desaturate }
    }
    Image {
        id: img
        anchors.fill: parent
        onStatusChanged: if (status === Image.Ready) { t.shown = source; thumbs.notePainted() }
        source: { void t.rev; void t.bucket; if (t.path === "") return ""; t.asked = t.path; return thumbs.url(t.path, t.bucket) }
        sourceSize: Qt.size(t.bucket, Math.round(t.bucket * 2 / 3))
        asynchronous: true
        fillMode: t.shape
        opacity: status === Image.Ready ? t.imageOpacity : 0
        Behavior on opacity { NumberAnimation { duration: 220 } }
        layer.enabled: t.desaturate > 0
        layer.effect: MultiEffect { saturation: -t.desaturate }
    }
    Connections { target: thumbs; function onReady(p) { if (p === t.path) t.rev++ } }
}
