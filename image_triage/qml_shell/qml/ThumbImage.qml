import QtQuick
import QtQuick.Effects

// A photo's thumbnail from the disk cache (see thumbs.py). Shows nothing until the file exists; the service says when it does.
Item {
    id: t
    property string path
    property int bucket: 240             // thumbnail width to ask for (a bucket size, so neighbours share cache files)
    property int fillMode: Image.PreserveAspectCrop
    property real imageOpacity: 1
    property real desaturate: 0          // 0..1, like CSS grayscale()
    readonly property int status: img.status
    property int rev: 0
    property string asked: ""
    property url shown                   // the last picture that finished loading: stays up while the next size (or stand-in) loads, so a size change does not flash blank

    function release() { if (asked !== "") { thumbs.unwant(asked); asked = "" } }
    Component.onDestruction: release()
    onPathChanged: { release(); shown = ""; rev++ }

    Image {
        anchors.fill: parent
        source: t.shown
        asynchronous: true
        sourceSize: Qt.size(t.bucket, Math.round(t.bucket * 2 / 3))
        fillMode: t.fillMode
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
        fillMode: t.fillMode
        opacity: status === Image.Ready ? t.imageOpacity : 0
        Behavior on opacity { NumberAnimation { duration: 220 } }
        layer.enabled: t.desaturate > 0
        layer.effect: MultiEffect { saturation: -t.desaturate }
    }
    Connections { target: thumbs; function onReady(p) { if (p === t.path) t.rev++ } }
}
