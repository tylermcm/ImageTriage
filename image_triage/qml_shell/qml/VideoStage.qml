import QtQuick
import QtMultimedia

// A video clip on the viewer's stage (Viewer.qml shows it in place of the photo when the row is a clip). It opens paused on its first
// frame, so moving through a folder never starts sound; K or the play button plays it. `step(n)` moves n frames (pausing first),
// `seek(ms)` jumps, and `stop()` lets go of the file when the viewer moves on. The frame rate comes from `clips.info` (clips.py).
Item {
    id: vs
    objectName: "videoStage"
    property string path: ""
    property real fps: 0
    property bool muted: false
    property real volume: 1.0
    readonly property alias player: mp
    readonly property bool playing: mp.playbackState === MediaPlayer.PlayingState
    readonly property real position: mp.position
    readonly property real duration: mp.duration
    readonly property bool ready: mp.mediaStatus === MediaPlayer.LoadedMedia || mp.mediaStatus === MediaPlayer.BufferedMedia
                                  || mp.mediaStatus === MediaPlayer.BufferingMedia || mp.mediaStatus === MediaPlayer.EndOfMedia
    readonly property bool failed: mp.mediaStatus === MediaPlayer.InvalidMedia
    readonly property string error: mp.errorString
    readonly property real frameMs: fps > 0 ? 1000 / fps : 1000 / 30
    property bool resumeAfterScrub: false

    function toggle() { if (!ready) return; if (playing) mp.pause(); else { if (mp.mediaStatus === MediaPlayer.EndOfMedia) mp.setPosition(0); mp.play() } }
    function pause() { if (playing) mp.pause() }
    function seek(ms) { if (!ready) return; mp.setPosition(Math.max(0, Math.min(mp.duration, Math.round(ms)))) }
    function step(frames) { if (!ready) return; mp.pause(); seek(mp.position + frames * frameMs) }
    function skip(seconds) { seek(mp.position + seconds * 1000) }
    // a drag on the scrub bar: it pauses while it lasts, and plays on afterwards when it was playing
    function beginScrub() { resumeAfterScrub = playing; mp.pause() }
    function endScrub() { if (resumeAfterScrub) mp.play(); resumeAfterScrub = false }
    function stop() { mp.stop() }

    onPathChanged: {
        mp.stop()
        mp.source = path ? clips.url(path) : ""
    }
    MediaPlayer {
        id: mp
        videoOutput: out
        audioOutput: AudioOutput { muted: vs.muted; volume: vs.volume }
        onMediaStatusChanged: if (mediaStatus === MediaPlayer.LoadedMedia && playbackState === MediaPlayer.StoppedState) pause()   // its first frame, paused
    }
    VideoOutput {
        id: out
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectFit
    }
}
