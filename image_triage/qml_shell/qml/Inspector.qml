import QtQuick
import QtQuick.Shapes

// The inspector: one continuous column (no boxes) with a sliding three-way tab control. Overview, Quality, AI.
Item {
    id: insp
    property int row: 0
    property int tab: 0
    property real fullW: 352
    readonly property real p: Math.max(0, Math.min(1, width / fullW))
    readonly property var cur: { void photos.revision; void insp.row; return photos.get(insp.row) }
    readonly property bool has: cur && cur.path !== undefined
    signal step(int delta)
    clip: true

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
                    Text { height: 13; verticalAlignment: Text.AlignVCenter; text: insp.has ? (insp.cur.isRaw ? "RAW + JPG" : insp.cur.ext) + " \u00b7 " + insp.cur.pixels : ""; color: theme.fg3; font.pointSize: UiState.pt(10.8333) }
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
                        Histogram { anchors.fill: parent; anchors.margins: 5 } }
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
                            model: insp.has ? [["Camera", insp.cur.camera], ["Lens", insp.cur.lens], ["Exposure", insp.cur.exposure], ["Focal length", insp.cur.focal], ["Captured", insp.cur.captured], ["Pixels", insp.cur.pixels]] : []
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
            function tone(v) { return v >= 70 ? ["#0fb89a", theme.win] : v >= 50 ? ["#e6ac3d", theme.rev] : ["#ef6856", theme.rej] }
            Column {
                id: qCol; width: parent.width
                Item {
                    width: parent.width; height: 224.8
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 14; height: 13; verticalAlignment: Text.AlignVCenter; text: "TECHNICAL CHECKS"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    Column { id: checks; x: 16; y: 37; width: parent.width - 32
                        Repeater {
                            model: insp.has ? [
                                ["Focus", insp.cur.focus >= 70 ? "Sharp" : insp.cur.focus >= 50 ? "Slightly soft" : "Soft", insp.cur.focus],
                                ["Exposure", insp.cur.expo, ({ Balanced: 88, Bright: 55, Dark: 48 })[insp.cur.expo]],
                                ["Noise", insp.cur.noise, ({ Low: 90, Medium: 60 })[insp.cur.noise]],
                                ["Motion blur", "None detected", 96]] : []
                            delegate: Item {
                                required property var modelData
                                width: checks.width; height: 43.2          // 18.2 + 5 + 6, then 14 below
                                Text { y: 0; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData[0]; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                Text { anchors.right: parent.right; y: 0; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData[1]; color: theme.fg2; font.pixelSize: 13 }
                                Meter { y: 23.2; width: parent.width; height: 6; value: modelData[2] / 100; threshold: 0.6; spanTrack: false; stops: quality.tone(modelData[2]) }
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
                        Histogram { anchors.fill: parent; three: true }
                        Accessible.name: "Histogram of the selected photo"; Accessible.role: Accessible.Graphic }
                }
            }
        }

        // ---- AI ------------------------------------------------------------------------------------------------------
        Flickable {
            id: ai
            readonly property bool on: insp.tab === 2
            property real shown: on && insp.has ? insp.cur.ai / 100 : 0
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
                        Text { anchors.centerIn: parent; text: insp.has ? String(insp.cur.ai) : ""; color: theme.fg; font.pixelSize: 28; font.weight: Font.DemiBold; font.letterSpacing: -0.84 }
                    }
                    Column {
                        x: 128; anchors.verticalCenter: ringBox.verticalCenter; width: parent.width - 144; spacing: 3
                        Text { height: 19; verticalAlignment: Text.AlignVCenter; text: !insp.has ? "" : insp.cur.decision === "rej" ? "Reject" : insp.cur.ai >= 85 ? "Winner candidate" : insp.cur.ai >= 65 ? "Keep" : "Review"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold }
                        Text { height: 18.2; verticalAlignment: Text.AlignVCenter; text: !insp.has ? "" : insp.cur.ai >= 85 ? "High confidence" : insp.cur.ai >= 65 ? "Medium confidence" : "Low confidence, take a look"; color: theme.fg2; font.pixelSize: 13 }
                    }
                }
                Item {
                    width: parent.width; height: 14 + 13 + 10 + whyCol.implicitHeight + 14 + 2     // padding, heading, gap, list, padding, two borders
                    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 15; height: 13; verticalAlignment: Text.AlignVCenter; text: "WHY"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    Column { id: whyCol; x: 16; y: 38; width: parent.width - 32; spacing: 2
                        Repeater {
                            model: insp.has ? [
                                [insp.cur.focus >= 70, insp.cur.focus >= 70 ? "Subject is sharp." : "Focus is a little soft.", "Focus score " + insp.cur.focus + " of 100."],
                                [insp.cur.expo === "Balanced", insp.cur.expo + " exposure.", insp.cur.expo === "Bright" ? "Highlights close to clipping." : "Highlights well protected."],
                                [true, "Strong composition.", "Horizon and subject sit on thirds."]] : []
                            delegate: Item {
                                required property var modelData
                                width: whyCol.width; height: why.implicitHeight + 14
                                readonly property color tone: modelData[0] ? theme.win : theme.rev
                                Rectangle { x: 0; y: 8; width: 16; height: 16; radius: 8; color: Qt.alpha(parent.tone, 0.22)
                                    Item { x: 5; y: 3; width: 5.6; height: 8.6; rotation: 45
                                        Rectangle { x: 4; width: 1.6; height: 8.6; color: tone }
                                        Rectangle { y: 7; width: 5.6; height: 1.6; color: tone } } }
                                Text { id: why; x: 26; y: 8; width: parent.width - 26; wrapMode: Text.WordWrap; textFormat: Text.StyledText; color: theme.fg2; font.pixelSize: 13; lineHeight: 18.2; lineHeightMode: Text.FixedHeight
                                    text: "<font color='" + theme.fg + "'>" + modelData[1] + "</font> " + modelData[2] }
                            }
                        }
                    }
                }
                Item {
                    width: parent.width; height: 103.3
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; y: 14; height: 13; verticalAlignment: Text.AlignVCenter; text: "IN THIS GROUP"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    Row { x: 16; y: 37; spacing: 4
                        Repeater { model: 4
                            delegate: Item {
                                required property int index
                                width: (insp.fullW - 32 - 12) / 4; height: width * 2 / 3
                                readonly property var it: { void photos.revision; return photos.get((insp.row + index) % Math.max(1, photos.shown)) }
                                Rectangle { visible: index === 0; x: -2; y: -2; width: parent.width + 4; height: parent.height + 4; radius: 8; color: theme.accent }   // box-shadow spread
                                RoundClip { anchors.fill: parent; radius: 6; opacity: index === 0 ? 1 : 0.55
                                    Rectangle { anchors.fill: parent; color: theme.s3 }
                                    ThumbImage { anchors.fill: parent; bucket: 160; path: it && it.path ? it.path : "" } }
                            }
                        }
                    }
                }
            }
        }
    }
}
