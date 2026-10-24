import QtQuick

// A photo's thumbnail from the disk cache (see thumbs.py). Shows nothing until the file exists; the service says when it does.
Item {
    id: t
    property string path
    property int bucket: 240             // thumbnail width to ask for (a bucket size, so neighbours share cache files)
    property int fillMode: Image.PreserveAspectCrop
    property real imageOpacity: 1
    readonly property int status: img.status
    property int rev: 0
    property string asked: ""

    function release() { if (asked !== "") { thumbs.unwant(asked); asked = "" } }
    Component.onDestruction: release()
    onPathChanged: { release(); rev++ }

    Image {
        id: img
        anchors.fill: parent
        source: { void t.rev; void t.bucket; if (t.path === "") return ""; t.asked = t.path; return thumbs.url(t.path, t.bucket) }
        sourceSize: Qt.size(t.bucket, Math.round(t.bucket * 2 / 3))
        asynchronous: true
        fillMode: t.fillMode
        opacity: status === Image.Ready ? t.imageOpacity : 0
        Behavior on opacity { NumberAnimation { duration: 220 } }
    }
    Connections { target: thumbs; function onReady(p) { if (p === t.path) t.rev++ } }
}
