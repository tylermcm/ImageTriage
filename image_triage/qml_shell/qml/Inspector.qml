import QtQuick
import QtQuick.Shapes

// The inspector: one continuous column (no boxes) with a sliding three-way tab control. Overview, Quality, AI.
Item {
    id: insp
    objectName: "inspector"
    property int row: 0
    property int tab: 0
    property real fullW: 352
    readonly property real p: Math.max(0, Math.min(1, width / fullW))
    readonly property var cur: { void photos.revision; void insp.row; return photos.get(insp.row) }
    readonly property bool has: cur && cur.path !== undefined
    signal step(int delta)
    signal analyze()                     // the AI tab's button, for a folder with no saved AI results
    signal pick(int row)                 // a photo of the AI group was clicked
    clip: true

    // What the Quality and AI tabs show. `facts` is the photo measured (inspection.py, only while one of those tabs is showing); `aiInfo` is the AI's verdict on it
    // from the folder's saved report (ai_scores.py). The mockup's sample photos carry both ready-made.
    readonly property string curPath: has ? cur.path : ""
    property var facts: ({ ready: false })
    readonly property var aiInfo: { void photos.revision; void aiScores.revision; return !has ? ({ has: false, state: "none" }) : (cur.aiCall !== undefined ? cur.aiCall : aiScores.get(curPath)) }
    readonly property var checkRows: {
        if (facts.ready) return facts.checks
        var text = !has ? "" : facts.failed ? "Couldn\u2019t read this photo" : "Measuring\u2026"
        return !has ? [] : ["Focus", "Exposure", "Noise", "Motion blur"].map(function (n) { return { name: n, text: text, value: 0, tone: -1 } })
    }
    readonly property var whyRows: {                // the AI's reasons, then what was measured
        var out = []
        if (aiInfo.has) for (var i = 0; i < aiInfo.reasons.length; ++i) out.push(aiInfo.reasons[i])
        if (facts.ready) for (var j = 0; j < facts.reasons.length; ++j) out.push(facts.reasons[j])
        return out
    }
    function refreshFacts() {
        if (!has) { facts = ({ ready: false }); return }
        if (cur.measured !== undefined) { facts = cur.measured; return }
        if (tab === 0) { facts = ({ ready: false }); return }                       // nothing is measured while the Overview is showing
        facts = inspection.get(curPath)
        if (!facts.ready && !facts.failed) inspection.request(curPath)
    }
    function esc(text) { return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;") }
    // The photo's camera, lens, exposure, capture time and pixel size (capture.py), read for every photo; the mockup's sample photos carry theirs.
    property var cap: ({ ready: false })
    function refreshCapture() {
        if (!has) { cap = ({ ready: false }); return }
        if (cur.capture !== undefined) { cap = cur.capture; return }
        cap = captureData.get(curPath)
        if (!cap.ready && !cap.failed) captureData.request(curPath)
    }
    function capText(key) { return !cap.ready ? (cap.failed ? "\u2014" : "\u2026") : (cap[key] ? cap[key] : "\u2014") }
    onCurPathChanged: { refreshFacts(); refreshCapture() }
    onTabChanged: refreshFacts()
    Component.onCompleted: { refreshFacts(); refreshCapture() }
    Connections { target: inspection; function onReady(p) { if (p === insp.curPath) insp.refreshFacts() } }
    Connections { target: captureData; function onReady(p) { if (p === insp.curPath) insp.refreshCapture() } }

    Rectangle { anchors.fill: parent; color: theme.s2 }
    Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: theme.lineSoft }

    Item {
        id: inner
        width: insp.fullW; height: parent.height
        anchors.right: parent.right
        anchors.rightMargin: -(1 - insp.p) * 20
        opacity: insp.p

        Item {
            id: head
            width: parent.width; height: 104.2         // 12 + 35.2 + 10 + 36 + 10 + 1
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
            Item {
                x: 16; y: 12; width: parent.width - 32; height: 35.2
                Column {
                    width: parent.width - 120; height: 35.2
                    Text { width: parent.width; height: 17; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; text: insp.has ? (insp.cur.name.indexOf(".") >= 0 ? insp.cur.name : insp.cur.name + "." + insp.cur.ext) : "No photo"
                           color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: -0.14 }
                    Item { width: 1; height: 4 }
                    Text { height: 13; verticalAlignment: Text.AlignVCenter; text: !insp.has ? "" : (insp.cur.isRaw && (insp.cur.companions === undefined || insp.cur.companions.length > 0) ? "RAW + JPG" : insp.cur.ext) + (insp.cap.ready && insp.cap.pixels ? " \u00b7 " + insp.cap.pixels : ""); color: theme.fg3; font.pointSize: UiState.pt(10.8333) }
                }
                Row {
                    anchors.right: parent.right; spacing: 8; height: 35.2
                    Item { y: 1.6; width: 32; height: 32; Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, pb.hovered ? 0.08 : 0) }
                        Icon { anchors.centerIn: parent; name: "chevL"; size: 18; color: theme.fg }
                        HoverHandler { id: pb; cursorShape: Qt.PointingHandCursor } TapHandler { onTapped: insp.step(-1) } Accessible.name: "Previous photo"; Accessible.role: Accessible.Button }
                    Text { height: 35.2; verticalAlignment: Text.AlignVCenter; text: insp.has ? insp.cur.position + " / " + insp.cur.count : ""; color: theme.fg3; font.pointSize: UiState.pt(10.8333) }
                    Item { y: 1.6; width: 32; height: 32; Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, nb.hovered ? 0.08 : 0) }
                        Icon { anchors.centerIn: parent; name: "chevR"; size: 18; color: theme.fg }
                        HoverHandler { id: nb; cursorShape: Qt.PointingHandCursor } TapHandler { onTapped: insp.step(1) } Accessible.name: "Next photo"; Accessible.role: Accessible.Button }
                }
            }
            Seg { x: 16; y: 57.2; width: parent.width - 32; fill: true; labels: ["Overview", "Quality", "AI"]; current: insp.tab; onActivated: (i) => insp.tab = i }
        }

        // ---- Overview ------------------------------------------------------------------------------------------------
        Flickable {
            id: overview
            readonly property bool on: insp.tab === 0
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: ovCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            transform: Translate { y: overview.on ? 0 : 8; Behavior on y { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } } }
            Behavior on opacity { NumberAnimation { duration: 250 } }
            Column {
                id: ovCol; width: parent.width
                Item {
                    width: parent.width; height: width * 2 / 3
                    Rectangle { anchors.fill: parent; color: theme.base }
                    ThumbImage { anchors.fill: parent; bucket: 800; path: insp.has ? insp.cur.path : "" }
                    Rectangle { x: 10; y: parent.height - 54; width: 112; height: 44; radius: 9; color: Qt.rgba(0, 0, 0, 0.45)
                        Histogram { objectName: "histogramPreview"; anchors.fill: parent; anchors.margins: 5; path: insp.has ? insp.cur.path : "" } }
                }
                Item {
                    width: parent.width; height: 176.2
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 14; height: 13; verticalAlignment: Text.AlignVCenter; text: "DECISION"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    Row {
                        x: 16; y: 37; width: parent.width - 32; spacing: 6
                        Repeater {
                            model: [{ k: "win", t: "Winner", i: "star", key: "W", c: theme.win }, { k: "keep", t: "Keep", i: "check", key: "K", c: theme.keep }, { k: "rej", t: "Reject", i: "x", key: "X", c: theme.rej }]
                            delegate: Rectangle {
                                required property var modelData
                                readonly property bool on: insp.has && insp.cur.decision === modelData.k
                                width: (insp.fullW - 32 - 12) / 3; height: 88.2; radius: 12
                                color: on ? Qt.alpha(modelData.c, 0.16) : theme.base
                                border.color: on ? Qt.alpha(modelData.c, 0.55) : (hh.hovered ? theme.line : theme.lineSoft)
                                scale: tp.pressed ? 0.96 : 1
                                Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }
                                Behavior on color { ColorAnimation { duration: 180 } }
                                Icon { x: (parent.width - 20) / 2; y: 11; name: modelData.i; size: 20; color: on ? modelData.c : (hh.hovered ? theme.fg : theme.fg2) }
                                Text { x: 0; y: 37; width: parent.width; height: 18.2; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                                       text: modelData.t; font.pixelSize: 13; font.weight: Font.Medium; color: on ? modelData.c : (hh.hovered ? theme.fg : theme.fg2) }
                                Item {   // the key hint: a 10px key cap, border 1 with a 2px bottom
                                    x: (parent.width - width) / 2; y: 61.2; width: kt.implicitWidth + 12; height: 18
                                    Rectangle { anchors.fill: parent; radius: 5; color: "transparent"; border.color: theme.line }
                                    Rectangle { x: 1; y: parent.height - 2.5; width: parent.width - 2; height: 1.5; radius: 1; color: theme.line }
                                    Text { id: kt; anchors.centerIn: parent; anchors.verticalCenterOffset: -0.5; text: modelData.key; font.pixelSize: 10; font.weight: Font.Medium; color: theme.fg3 }
                                }
                                HoverHandler { id: hh; cursorShape: Qt.PointingHandCursor }
                                TapHandler { id: tp; onTapped: photos.setDecision(insp.row, modelData.k) }
                                Accessible.role: Accessible.Button; Accessible.name: modelData.t
                            }
                        }
                    }
                    Item {
                        x: 16; y: 137.2; width: parent.width - 32; height: 24
                        Row {
                            spacing: 2
                            Repeater {
                                model: 5
                                delegate: Item {
                                    required property int index
                                    width: 27; height: 24
                                    readonly property bool on: insp.has && insp.cur.rating >= index + 1
                                    scale: sh.hovered ? 1.2 : 1
                                    Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }
                                    Text { anchors.centerIn: parent; text: "\u2605"; font.pixelSize: 20; color: on ? theme.star : Qt.alpha(theme.lift, 0.24) }
                                    HoverHandler { id: sh; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: photos.setRating(insp.row, index + 1) }
                                    Accessible.role: Accessible.Button; Accessible.name: (index + 1) + " stars"
                                }
                            }
                        }
                        Text { anchors.right: parent.right; height: 24; verticalAlignment: Text.AlignVCenter; text: insp.has && insp.cur.rating ? insp.cur.rating + " of 5" : "Unrated"; color: theme.fg3; font.pointSize: UiState.pt(10.8333) }
                    }
                }
                Item {
                    width: parent.width; height: 196.1
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 14; height: 13; verticalAlignment: Text.AlignVCenter; text: "CAPTURE"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    Column {
                        x: 16; y: 37; spacing: 7
                        Repeater {
                            model: insp.has ? [["Camera", insp.capText("camera")], ["Lens", insp.capText("lens")], ["Exposure", insp.capText("exposure")], ["Focal length", insp.capText("focal")], ["Captured", insp.capText("captured")], ["Pixels", insp.capText("pixels")]] : []
                            delegate: Row {
                                required property var modelData
                                spacing: 12
                                Text { width: 96; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData[0]; color: theme.fg3; font.pixelSize: 13 }
                                Text { height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData[1]; color: theme.fg; font.pixelSize: 13 }
                            }
                        }
                    }
                }
            }
        }

        // ---- Quality -------------------------------------------------------------------------------------------------
        Flickable {
            id: quality
            readonly property bool on: insp.tab === 1
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: qCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            transform: Translate { y: quality.on ? 0 : 8; Behavior on y { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } } }
            Behavior on opacity { NumberAnimation { duration: 250 } }
            function tone(t) { return t === 0 ? ["#0fb89a", theme.win] : t === 1 ? ["#e6ac3d", theme.rev] : t === 2 ? ["#ef6856", theme.rej] : [theme.fg3] }
            Column {
                id: qCol; width: parent.width
                Item {
                    width: parent.width; height: 224.8
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 14; height: 13; verticalAlignment: Text.AlignVCenter; text: "TECHNICAL CHECKS"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    Column { id: checks; x: 16; y: 37; width: parent.width - 32
                        Repeater {
                            model: insp.checkRows
                            delegate: Item {
                                required property var modelData
                                width: checks.width; height: 43.2          // 18.2 + 5 + 6, then 14 below
                                Text { y: 0; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData.name; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                Text { anchors.right: parent.right; y: 0; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData.text; color: modelData.tone < 0 ? theme.fg3 : theme.fg2; font.pixelSize: 13 }
                                Meter { y: 23.2; width: parent.width; height: 6; value: modelData.value / 100; threshold: 0.6; spanTrack: false; stops: quality.tone(modelData.tone) }
                            }
                        }
                    }
                }
                Item {
                    width: parent.width; height: 136
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 14; height: 13; verticalAlignment: Text.AlignVCenter; text: "HISTOGRAM"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    RoundClip { x: 16; y: 37; width: parent.width - 32; height: 84; radius: 10
                        Rectangle { anchors.fill: parent; color: theme.base }
                        Histogram { objectName: "histogramQuality"; anchors.fill: parent; three: true; path: insp.has ? insp.cur.path : "" } }
                }
            }
        }

        // ---- AI ------------------------------------------------------------------------------------------------------
        Flickable {
            id: ai
            readonly property bool on: insp.tab === 2
            property real shown: on && insp.aiInfo.has ? insp.aiInfo.score / 100 : 0
            Behavior on shown { NumberAnimation { duration: 1000; easing.type: Easing.OutCubic } }
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: aiCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            transform: Translate { y: ai.on ? 0 : 8; Behavior on y { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } } }
            Behavior on opacity { NumberAnimation { duration: 250 } }
            Column {
                id: aiCol; width: parent.width
                Item {
                    width: parent.width; height: 128
                    Item {
                        id: ringBox; x: 16; y: 16; width: 96; height: 96
                        Shape {
                            anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
                            ShapePath {
                                fillRule: ShapePath.OddEvenFill; strokeColor: "transparent"
                                // CSS conic gradients run clockwise from the angle, Qt's counter-clockwise: stops are mirrored (q = 1 - c), start at 9 o'clock (from -90deg)
                                fillGradient: ConicalGradient {
                                    centerX: 48; centerY: 48; angle: 180
                                    GradientStop { position: 0.0; color: theme.s3 }
                                    GradientStop { position: Math.max(0.0, Math.min(0.999, 1 - ai.shown)); color: theme.s3 }
                                    GradientStop { position: Math.max(0.001, Math.min(0.9995, 1 - ai.shown + 0.0005)); color: "#d58ae8" }
                                    GradientStop { position: 1.0; color: theme.accent }
                                }
                                PathSvg { path: Icons.circle(48, 48, 48) + Icons.circle(48, 48, 39) }
                            }
                        }
                        Text { anchors.centerIn: parent; text: !insp.has ? "" : insp.aiInfo.has ? String(insp.aiInfo.score) : insp.aiInfo.state === "loading" ? "\u2026" : "\u2013"
                               color: insp.aiInfo.has ? theme.fg : theme.fg3; font.pixelSize: 28; font.weight: Font.DemiBold; font.letterSpacing: -0.84 }
                    }
                    Column {
                        x: 128; anchors.verticalCenter: ringBox.verticalCenter; width: parent.width - 144; spacing: 3
                        Text { width: parent.width; height: 19; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold
                               text: !insp.has ? "" : insp.aiInfo.has ? insp.aiInfo.call : insp.aiInfo.state === "loading" ? "Looking for AI results" : insp.aiInfo.state === "missing" ? "Not scored" : "Not analyzed yet" }
                        Text { width: parent.width; verticalAlignment: Text.AlignVCenter; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight; color: theme.fg2; font.pixelSize: 13
                               text: !insp.has ? "" : insp.aiInfo.has ? insp.aiInfo.line : insp.aiInfo.state === "loading" ? "" : insp.aiInfo.state === "missing" ? "This photo isn\u2019t in the folder\u2019s AI results." : "Analyze the folder to score its photos." }
                        Item { visible: insp.has && !insp.aiInfo.has && insp.aiInfo.state !== "loading"; width: 1; height: 8 }
                        SButton { objectName: "aiAnalyzeButton"; visible: insp.has && !insp.aiInfo.has && insp.aiInfo.state !== "loading"; text: "Analyze folder\u2026"; h: 28; onClicked: insp.analyze() }
                    }
                }
                Item {
                    width: parent.width; height: 14 + 13 + 10 + whyCol.implicitHeight + 14 + 2     // padding, heading, gap, list, padding, two borders
                    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 15; height: 13; verticalAlignment: Text.AlignVCenter; text: insp.aiInfo.has ? "WHY" : "WHAT WE MEASURED"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    Column { id: whyCol; x: 16; y: 38; width: parent.width - 32; spacing: 2
                        Repeater {
                            model: insp.whyRows
                            delegate: Item {
                                required property var modelData
                                width: whyCol.width; height: why.implicitHeight + 14
                                readonly property color tone: modelData.good ? theme.win : theme.rev
                                Rectangle { x: 0; y: 8; width: 16; height: 16; radius: 8; color: Qt.alpha(parent.tone, 0.22)
                                    Item { visible: modelData.good; x: 5; y: 3; width: 5.6; height: 8.6; rotation: 45
                                        Rectangle { x: 4; width: 1.6; height: 8.6; color: tone }
                                        Rectangle { y: 7; width: 5.6; height: 1.6; color: tone } }
                                    Rectangle { visible: !modelData.good; x: 7.2; y: 3.4; width: 1.6; height: 5.8; radius: 0.8; color: tone }       // "!" for a finding that is not good
                                    Rectangle { visible: !modelData.good; x: 7.2; y: 10.4; width: 1.6; height: 1.6; radius: 0.8; color: tone } }
                                Text { id: why; x: 26; y: 8; width: parent.width - 26; wrapMode: Text.WordWrap; textFormat: Text.StyledText; color: theme.fg2; font.pixelSize: 13; lineHeight: 18.2; lineHeightMode: Text.FixedHeight
                                    text: "<font color='" + theme.fg + "'>" + insp.esc(modelData.head) + "</font> " + insp.esc(modelData.detail) }
                            }
                        }
                        Text { visible: insp.has && insp.whyRows.length === 0; width: whyCol.width; height: 32; verticalAlignment: Text.AlignVCenter; color: theme.fg3; font.pixelSize: 13
                               text: insp.facts.failed ? "This photo couldn\u2019t be read." : "Measuring this photo\u2026" }
                    }
                }
                Item {
                    objectName: "aiGroup"
                    visible: insp.aiInfo.has && insp.aiInfo.group !== undefined && insp.aiInfo.group.length > 1
                    width: parent.width; height: visible ? 103.3 : 0
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 14; height: 13; verticalAlignment: Text.AlignVCenter; text: "IN THIS GROUP"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    Text { visible: insp.has && insp.cur.aiCall === undefined; anchors.right: parent.right; anchors.rightMargin: 16; y: 14; height: 13; verticalAlignment: Text.AlignVCenter    // the mockup's sample group has no count
                           text: insp.aiInfo.has ? insp.aiInfo.size + " photos" : ""; color: theme.fg3; font.pointSize: UiState.pt(10.5) }
                    Row { x: 16; y: 37; spacing: 4
                        Repeater { model: insp.aiInfo.has && insp.aiInfo.group !== undefined ? insp.aiInfo.group : []
                            delegate: Item {
                                required property var modelData
                                required property int index
                                objectName: "aiGroupPhoto-" + index
                                readonly property bool shown: modelData.row >= 0           // the photo is among the ones the grid shows (a filter may hide it)
                                width: (insp.fullW - 32 - 12) / 4; height: width * 2 / 3
                                Rectangle { visible: modelData.current; x: -2; y: -2; width: parent.width + 4; height: parent.height + 4; radius: 8; color: theme.accent }   // box-shadow spread
                                RoundClip { anchors.fill: parent; radius: 6; opacity: modelData.current ? 1 : shown ? 0.55 : 0.3
                                    Rectangle { anchors.fill: parent; color: theme.s3 }
                                    ThumbImage { anchors.fill: parent; bucket: 160; path: modelData.path } }
                                HoverHandler { enabled: shown && !modelData.current; cursorShape: Qt.PointingHandCursor }
                                TapHandler { enabled: shown && !modelData.current; onTapped: insp.pick(modelData.row) }
                                Accessible.role: Accessible.Button; Accessible.name: modelData.current ? "This photo" : "Another photo of the group"
                            }
                        }
                    }
                }
            }
        }
    }
}
