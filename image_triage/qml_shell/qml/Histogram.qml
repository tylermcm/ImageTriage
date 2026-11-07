import QtQuick
import QtQuick.Shapes

// The histogram of a photo: its brightness (on the preview), or its red, green and blue (`three`, on the Quality tab). The counts come from
// histograms.py, the Widgets app's own counting, for the photo at `path`; until they arrive (or if the photo cannot be read) nothing is drawn.
Item {
    id: h
    property string path: ""
    property var service: histograms
    property bool three: false
    property bool scopeStyle: false
    property color luma: Qt.rgba(1, 1, 1, 0.42)
    property color red: Qt.rgba(235 / 255, 100 / 255, 100 / 255, 0.42)
    property color green: Qt.rgba(80 / 255, 200 / 255, 140 / 255, 0.42)
    property color blue: Qt.rgba(90 / 255, 150 / 255, 1.0, 0.5)
    property var counts: ({ ready: false })
    readonly property bool has: counts.ready === true
    readonly property string summary: has ? counts.summary : ""

    function refresh() {
        if (path === "" || !visible || !service) { counts = ({ ready: false }); return }
        counts = service.get(path)
        if (!counts.ready) service.request(path)
    }
    onPathChanged: refresh()
    onVisibleChanged: if (visible) refresh()
    Component.onCompleted: refresh()
    Connections { target: h.service; function onReady(p) { if (p === h.path) h.refresh() } }
    onCountsChanged: scope.requestPaint()

    Canvas {
        id: scope
        anchors.fill: parent
        visible: h.scopeStyle && h.has
        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            ctx.lineWidth = 0.5; ctx.strokeStyle = "#242831"
            for (var x = 1; x < 5; ++x) {
                ctx.beginPath(); ctx.moveTo(width * x / 5, 0)
                ctx.lineTo(width * x / 5, height); ctx.stroke()
            }
            var keys = ["r", "g", "b"]
            var fills = ["rgba(229,99,91,0.333)", "rgba(88,196,142,0.333)", "rgba(104,169,255,0.4)"]
            var strokes = ["#e5635b", "#58c48e", "#68a9ff"]
            for (var c = 0; c < 3; ++c) {
                var bins = h.counts["scope_" + keys[c]] || h.counts[keys[c]] || []
                ctx.beginPath(); ctx.moveTo(0, height)
                for (var i = 0; i < bins.length; ++i)
                    ctx.lineTo(i * width / Math.max(1, bins.length - 1), height - Math.sqrt(bins[i]) * height * 14 / 15)
                ctx.lineTo(width, height); ctx.closePath()
                ctx.fillStyle = fills[c]; ctx.fill()
                ctx.strokeStyle = strokes[c]; ctx.stroke()
            }
        }
    }

    // the 64 counts as a closed outline, as SVG path text, in a 100 x 40 box (the baseline at 40, a full bar 2 units from the top)
    function outline(bins) {
        var n = bins ? bins.length : 0
        if (n === 0) return ""
        var d = "M0 40"
        for (var i = 0; i < n; ++i) d += "L" + ((i + 0.5) * 100 / n).toFixed(2) + " " + (40 - bins[i] * 38).toFixed(2)
        return d + "L100 40z"
    }

    Shape {
        visible: h.has && !h.scopeStyle
        width: 100; height: 40; preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: h.width / 100; yScale: h.height / 40 }
        ShapePath { strokeColor: "transparent"; fillColor: h.three ? h.red : h.luma; PathSvg { path: h.outline(h.three ? h.counts.r : h.counts.luma) } }
        ShapePath { strokeColor: "transparent"; fillColor: h.three ? h.green : "transparent"; PathSvg { path: h.three ? h.outline(h.counts.g) : "" } }
        ShapePath { strokeColor: "transparent"; fillColor: h.three ? h.blue : "transparent"; PathSvg { path: h.three ? h.outline(h.counts.b) : "" } }
    }
    Accessible.role: Accessible.Graphic
    Accessible.name: "Histogram of the selected photo"
    Accessible.description: h.summary
}
