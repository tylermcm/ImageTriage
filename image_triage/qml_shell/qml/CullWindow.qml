import QtQuick
import QtQuick.Shapes

// Cull this folder: one window with a step rail (Prepare, Analyze, Review, Apply). The model is cull_model.py (`cull`); this file only draws
// its `view`. Layout and sizes follow the mockup (scripts/qml_parity/cull.dc.html).
Backdrop {
    id: root
    objectName: "cullWindow"
    readonly property var v: cull.view
    readonly property string page: v.page
    shown: v.window === "cull"
    onDismissed: cull.close()
    z: 106

    readonly property real winW: Math.min(1180, width - 64)
    readonly property real winH: Math.min(780, height - 72)

    component Lbl: Text { color: theme.fg2; font.pixelSize: 13 }
    component H3: Text { property alias title: h.text; id: h; height: 13; verticalAlignment: Text.AlignVCenter; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
    component Ring: Item {
        id: ring
        property color c: theme.accent
        property real pct: 0
        property real size: 24
        width: size; height: size
        Shape { anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
            ShapePath { strokeColor: theme.s3; strokeWidth: 3; fillColor: "transparent"; PathAngleArc { centerX: size / 2; centerY: size / 2; radiusX: size / 2 - 2; radiusY: size / 2 - 2; startAngle: 0; sweepAngle: 360 } }
            ShapePath { strokeColor: ring.c; strokeWidth: 3; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                        PathAngleArc { centerX: size / 2; centerY: size / 2; radiusX: size / 2 - 2; radiusY: size / 2 - 2; startAngle: -90; sweepAngle: 360 * pct / 100 } } }
    }
    // a card on the page: a raised surface with a 1px ring
    component Card: Rectangle { radius: 14; color: theme.s1; border.color: theme.lineSoft }
    component Dot: Rectangle { property color c: theme.fg3; width: 8; height: 8; radius: 4; color: c }
    function dotColor(k) { return k === "ok" ? theme.win : k === "bad" ? theme.rej : theme.rev }

    DialogCard {
        id: win
        shown: root.shown
        width: root.winW; height: root.winH
        x: (parent.width - width) / 2; y: (parent.height - height) / 2
        RoundClip {
            anchors.fill: parent; anchors.margins: 1; radius: 15
            // ---- the rail ----
            Rectangle {
                id: rail
                width: 232; height: parent.height; color: theme.s1
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }
                Row { x: 20; y: 20; spacing: 11
                    Rectangle { width: 36; height: 36; radius: 12; color: Qt.tint(theme.s2, Qt.alpha(theme.accent, 0.16)); border.color: Qt.alpha(theme.accent, 0.28)
                        Icon { anchors.centerIn: parent; name: "spark"; size: 19; strokeWidth: 1.7; color: theme.accentFg } }
                    Column { anchors.verticalCenter: parent.verticalCenter
                        Text { text: "Cull"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15 }
                        Text { width: 150; text: root.v.folder; elide: Text.ElideRight; color: theme.fg3; font.pixelSize: 12 } }
                }
                Column {
                    x: 14; y: 74; width: parent.width - 28; spacing: 2
                    Repeater {
                        model: root.v.steps
                        delegate: Rectangle {
                            id: st
                            required property var modelData
                            width: parent.width; height: 53; radius: 11
                            color: modelData.state === "cur" ? Qt.alpha(theme.accent, 0.12) : "transparent"
                            Rectangle { x: 8; y: 13; width: 26; height: 26; radius: 13
                                color: st.modelData.state === "cur" ? theme.accent : st.modelData.state === "done" ? Qt.tint(theme.s2, Qt.alpha(theme.win, 0.22)) : theme.s2
                                border.color: st.modelData.state === "cur" ? "transparent" : st.modelData.state === "done" ? Qt.alpha(theme.win, 0.4) : theme.line
                                Text { visible: st.modelData.state !== "done"; anchors.centerIn: parent; text: st.modelData.n; font.pixelSize: 12; font.weight: Font.DemiBold; color: st.modelData.state === "cur" ? theme.onAccent : theme.fg3 }
                                Icon { visible: st.modelData.state === "done"; anchors.centerIn: parent; name: "check"; size: 14; strokeWidth: 2.6; color: theme.win } }
                            Column { x: 46; anchors.verticalCenter: parent.verticalCenter
                                Text { text: st.modelData.label; font.pixelSize: 13; font.weight: Font.Medium; color: st.modelData.state === "cur" ? theme.fg : st.modelData.state === "done" ? theme.fg2 : theme.fg3 }
                                Text { text: st.modelData.sub; font.pointSize: UiState.pt(11.5); color: theme.fg3 } }
                        }
                    }
                }
                Rectangle {
                    x: 14; width: parent.width - 28; height: pcol.implicitHeight + 24; radius: 12; color: theme.base; border.color: theme.lineSoft
                    y: parent.height - 14 - 40 - 10 - height
                    Column { id: pcol; x: 12; y: 12; width: parent.width - 24; spacing: 7
                        Repeater { model: ["It never picks winners for you", "Nothing is deleted without your say", "Every flag comes with its reason"]
                            delegate: Row { required property string modelData; spacing: 8; width: pcol.width
                                Icon { name: "check"; size: 14; strokeWidth: 2.2; color: theme.accentFg; y: 1 }
                                Text { width: parent.width - 22; text: modelData; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 12; lineHeight: 16.2; lineHeightMode: Text.FixedHeight } } } }
                }
                Item {
                    x: 14; y: parent.height - 14 - 40; width: parent.width - 28; height: 40
                    Rectangle { anchors.fill: parent; radius: 11; color: Qt.alpha(theme.lift, ch.hovered ? 0.07 : 0) }
                    Icon { x: 10; anchors.verticalCenter: parent.verticalCenter; path: "M4 7h10M18 7h2M4 17h2M10 17h10M16 9V5M8 19v-4"; size: 18; strokeWidth: 1.6; color: ch.hovered ? theme.fg : theme.fg2 }
                    Text { x: 38; anchors.verticalCenter: parent.verticalCenter; text: "AI Center"; font.pixelSize: 13; color: ch.hovered ? theme.fg : theme.fg2 }
                    Row { anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                        Dot { c: root.dotColor(root.v.centerDot); anchors.verticalCenter: parent.verticalCenter }
                        Text { text: root.v.centerText; font.pointSize: UiState.pt(11.5); color: theme.fg3 } }
                    HoverHandler { id: ch; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: cull.openCenter() }
                    Accessible.role: Accessible.Button; Accessible.name: "AI Center"
                }
            }
            // ---- the page ----
            Item {
                id: main
                x: rail.width; width: parent.width - rail.width; height: parent.height
                Column {
                    id: hd
                    x: 28; y: 23; width: parent.width - 56
                    Text { width: parent.width; text: root.v.title; color: theme.fg; font.pixelSize: 18; font.weight: Font.DemiBold; font.letterSpacing: -0.27 }
                    Text { visible: text !== ""; width: Math.min(560, parent.width); topPadding: 6; text: root.v.sub; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
                }
                Flickable {
                    id: body
                    y: hd.y + hd.height + 22; width: parent.width; height: parent.height - y - 60
                    contentWidth: width; contentHeight: pages.height + 18; clip: true; boundsBehavior: Flickable.StopAtBounds
                    Item {
                        id: pages
                        x: 28; width: body.width - 56; height: Math.max(prep.height, run.height, rev.height, apply.height, done.height)
                        // -------- prepare --------
                        Column {
                            id: prep
                            visible: root.page === "prep"; width: parent.width; spacing: 0
                            H3 { title: "WHAT WILL RUN"; width: parent.width }
                            Item { width: 1; height: 9 }
                            // job: cleanup
                            Card { id: j1; width: parent.width; height: Math.max(j1t.implicitHeight, 36) + 26
                                Rectangle { x: 14; y: 13; width: 36; height: 36; radius: 11; color: Qt.tint(theme.s2, Qt.alpha(theme.accent, 0.14)); border.color: theme.lineSoft
                                    Icon { anchors.centerIn: parent; name: "stack"; path: "M12 4l9 5-9 5-9-5zM3 14l9 5 9-5"; size: 19; color: theme.accentFg } }
                                Column { id: j1t; x: 64; y: 13; width: parent.width - 64 - 14 - 14 - 52; spacing: 2
                                    Text { text: "Cleanup and stacks"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                    Text { width: parent.width; text: "Groups near-identical frames into stacks and flags soft, clipped and blown-out ones against the rest of their stack. Needs no setup."; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight } }
                                Row { anchors.right: parent.right; anchors.rightMargin: 14; y: 13; height: 36; spacing: 8
                                    Dot { c: theme.win; anchors.verticalCenter: parent.verticalCenter }
                                    Text { text: "Ready"; color: theme.fg2; font.pointSize: UiState.pt(12.5); anchors.verticalCenter: parent.verticalCenter } } }
                            Item { width: 1; height: 8 }
                            // job: eyes
                            Card { id: j2; width: parent.width; height: Math.max(j2t.implicitHeight, 36) + 26
                                Rectangle { x: 14; y: 13; width: 36; height: 36; radius: 11; color: root.v.eyes.on ? Qt.tint(theme.s2, Qt.alpha(theme.accent, 0.14)) : theme.s2; border.color: theme.lineSoft
                                    Icon { anchors.centerIn: parent; path: "M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6zM12 14.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM4 4l16 16"; size: 19; color: root.v.eyes.on ? theme.accentFg : theme.fg2 } }
                                Column { id: j2t; x: 64; y: 13; width: parent.width - 64 - 14 - 14 - (root.v.rt.state === "missing" ? 78 : 38); spacing: 2
                                    Text { text: "Face and eyes-closed check"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                    Text { width: parent.width; text: root.v.eyes.desc; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight }
                                    Item { visible: root.v.rt.state === "installing"; width: parent.width; height: 28
                                        Rectangle { y: 10; width: parent.width; height: 5; radius: 2.5; color: theme.s3
                                            Rectangle { width: parent.width * root.v.rt.pct / 100; height: 5; radius: 2.5; gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: theme.accent } GradientStop { position: 1; color: "#d58ae8" } } } }
                                        Text { y: 14; text: root.v.rt.step; color: theme.fg3; font.pointSize: UiState.pt(12) } }
                                    Rectangle { visible: root.v.rt.state === "error"; width: parent.width; height: ecol.implicitHeight + 22; radius: 11; color: Qt.tint(theme.base, Qt.alpha(theme.rej, 0.10)); border.color: Qt.alpha(theme.rej, 0.35)
                                        Column { id: ecol; x: 12; y: 11; width: parent.width - 24; spacing: 3
                                            Text { text: root.v.rt.errTitle; color: theme.fg; font.pointSize: UiState.pt(12.5); font.weight: Font.Medium }
                                            Text { width: parent.width; text: root.v.rt.errBody; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 12; lineHeight: 18; lineHeightMode: Text.FixedHeight; bottomPadding: 6 }
                                            Row { spacing: 8
                                                SButton { visible: root.v.rt.errVc; text: "Get the update"; h: 28; hpad: 11; radius: 9; onClicked: Qt.openUrlExternally("https://aka.ms/vs/17/release/vc_redist.x64.exe") }
                                                SButton { text: "Check again"; h: 28; hpad: 11; radius: 9; onClicked: cull.recheckRuntime() } } } } }
                                Item { anchors.right: parent.right; anchors.rightMargin: 14; y: 13; width: 70; height: 36
                                    SButton { visible: root.v.rt.state === "missing"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "Set up AI"; variant: "primary"; h: 28; hpad: 11; radius: 9; onClicked: cull.setupRuntime() }
                                    SSwitch { visible: root.v.rt.state === "ready"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; checked: root.v.eyes.on; onToggled: cull.toggleEyes() } } }
                            Item { width: 1; height: 8 }
                            // job: semantic search
                            Card { id: j4; width: parent.width; height: Math.max(j4t.implicitHeight, 36) + 26
                                Rectangle { x: 14; y: 13; width: 36; height: 36; radius: 11; color: root.v.semantic.on ? Qt.tint(theme.s2, Qt.alpha(theme.accent, 0.14)) : theme.s2; border.color: theme.lineSoft
                                    Icon { anchors.centerIn: parent; name: "search"; size: 19; color: root.v.semantic.on ? theme.accentFg : theme.fg2 } }
                                Column { id: j4t; x: 64; y: 13; width: parent.width - 64 - 14 - 14 - 38; spacing: 2
                                    Text { text: "Semantic search"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                    Text { width: parent.width; text: root.v.semantic.desc; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight } }
                                SSwitch { visible: root.v.rt.state === "ready"; anchors.right: parent.right; anchors.rightMargin: 14; y: 13 + (36 - height) / 2; checked: root.v.semantic.on; onToggled: cull.toggleSemantic() } }
                            Item { width: 1; height: 8 }
                            // job: ranking
                            Card { id: j3; width: parent.width; height: Math.max(j3t.implicitHeight, 36) + 26
                                Rectangle { x: 14; y: 13; width: 36; height: 36; radius: 11; color: root.v.rank.on ? Qt.tint(theme.s2, Qt.alpha(theme.accent, 0.14)) : theme.s2; border.color: theme.lineSoft
                                    Icon { anchors.centerIn: parent; name: "bars"; size: 19; color: root.v.rank.on ? theme.accentFg : theme.fg2 } }
                                Column { id: j3t; x: 64; y: 13; width: parent.width - 64 - 14 - 14 - (root.v.rank.canSetup ? 144 : 38); spacing: 2
                                    Row { spacing: 5; Text { text: "Ranking"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium } Text { text: "· optional"; color: theme.fg3; font.pixelSize: 13 } }
                                    Text { width: parent.width; text: root.v.rank.desc; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight }
                                    Rectangle { visible: root.v.rank.ask; width: parent.width; height: acol.implicitHeight + 22; radius: 11; color: Qt.tint(theme.base, Qt.alpha(theme.rev, 0.09)); border.color: Qt.alpha(theme.rev, 0.32)
                                        Column { id: acol; x: 12; y: 11; width: parent.width - 24; spacing: 3
                                            Text { text: "Rank on the CPU?"; color: theme.fg; font.pointSize: UiState.pt(12.5); font.weight: Font.Medium }
                                            Text { width: parent.width; text: "Ranking runs two AI models. Without a usable GPU they are slow: about 7 seconds per frame on a fast workstation and over a minute per frame on a laptop. They score a quarter to a half of the folder, so a large folder can take hours."
                                                   wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 12; lineHeight: 18; lineHeightMode: Text.FixedHeight; bottomPadding: 6 }
                                            Row { spacing: 8
                                                SButton { text: "Rank on the CPU"; variant: "primary"; h: 28; hpad: 11; radius: 9; onClicked: cull.answerCpu(true) }
                                                SButton { text: "Not now"; h: 28; hpad: 11; radius: 9; onClicked: cull.answerCpu(false) } } } } }
                                Row { anchors.right: parent.right; anchors.rightMargin: 14; y: 13; height: 36; spacing: 8
                                    SButton { visible: root.v.rank.canSetup; anchors.verticalCenter: parent.verticalCenter; text: "Install models…"; h: 28; hpad: 11; radius: 9; onClicked: cull.openCenter() }
                                    SSwitch { anchors.verticalCenter: parent.verticalCenter; checked: root.v.rank.on; onToggled: cull.toggleRank() } } }
                            Item { width: 1; height: 16 }
                            Item { width: parent.width; height: 13
                                H3 { title: "HOW STRICT"; width: 200 }
                                Item { anchors.right: parent.right; width: tl.implicitWidth + 8; height: 18; y: -3
                                    Rectangle { anchors.fill: parent; radius: 6; color: Qt.alpha(theme.accent, th.hovered ? 0.12 : 0) }
                                    Text { id: tl; anchors.centerIn: parent; text: root.v.tuneLabel; color: theme.fg3; font.pointSize: UiState.pt(12); font.weight: Font.Medium }
                                    HoverHandler { id: th; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: cull.toggleTune() } } }
                            Item { width: 1; height: 9 }
                            Seg { width: parent.width; height: 38; fill: true; showExtras: false; labels: ["Careful", "Balanced", "Strict"]
                                  current: { for (var i = 0; i < root.v.sens.length; ++i) if (root.v.sens[i].on) return i; return 1 }
                                  onActivated: (i) => cull.setSens(root.v.sens[i].key) }
                            Text { width: parent.width; topPadding: 9; leftPadding: 2; text: root.v.sensText; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
                            Item { visible: root.v.tuneOpen; width: 1; height: 12 }
                            Card { visible: root.v.tuneOpen; width: parent.width; height: tcol.implicitHeight + 18
                                Column { id: tcol; x: 14; y: 4; width: parent.width - 28
                                    Repeater { model: root.v.tunes
                                        delegate: Item { id: tr; required property var modelData; width: tcol.width; height: 63
                                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                                            Column { y: 11; width: parent.width - 268 - 16; spacing: 2
                                                Text { text: tr.modelData.label; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                                Text { width: parent.width; text: tr.modelData.help; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12; lineHeight: 16.8; lineHeightMode: Text.FixedHeight } }
                                            SSlider { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; trackW: 200; from: tr.modelData.min; to: tr.modelData.max; value: tr.modelData.raw; text: tr.modelData.val
                                                      onMoved: (x) => cull.setVal(tr.modelData.key, Math.round(x)) } } }
                                    Item { width: parent.width; height: 48
                                        Text { y: 14; width: parent.width - 190; text: "Measured on two reviewed albums. Review them on your own genres before relying on them."; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12; lineHeight: 16.8; lineHeightMode: Text.FixedHeight }
                                        SButton { anchors.right: parent.right; y: 12; text: "Reset to measured values"; h: 28; hpad: 11; radius: 9; onClicked: cull.resetTune() } } } }
                        }
                        // -------- running --------
                        Column {
                            id: run
                            visible: root.page === "run"; width: parent.width; spacing: 6
                            Repeater { model: root.v.stages
                                delegate: Rectangle { id: sg; required property var modelData; width: run.width; height: 46; radius: 13
                                    color: modelData.state === "cur" ? theme.s1 : "transparent"; border.color: modelData.state === "cur" ? theme.lineSoft : "transparent"; opacity: modelData.state === "skip" ? 0.55 : 1
                                    Item { x: 14; anchors.verticalCenter: parent.verticalCenter; width: 24; height: 24
                                        Rectangle { anchors.fill: parent; radius: 12; visible: sg.modelData.state !== "cur"; color: sg.modelData.state === "done" ? Qt.tint(theme.s2, Qt.alpha(theme.win, 0.22)) : "transparent"
                                            border.width: 1.5; border.color: sg.modelData.state === "done" ? Qt.alpha(theme.win, 0.4) : theme.line }
                                        Icon { visible: sg.modelData.state === "done"; anchors.centerIn: parent; name: "check"; size: 13; strokeWidth: 2.8; color: theme.win }
                                        // the logo's ring fills 73% of its box, so 33 gives about the 24 the old ring had
                                        LogoMark { visible: sg.modelData.state === "cur"; anchors.centerIn: parent; size: 33; spinning: visible } }
                                    Text { x: 51; anchors.verticalCenter: parent.verticalCenter; text: sg.modelData.label; font.pixelSize: 13; font.weight: Font.Medium; color: sg.modelData.state === "cur" ? theme.fg : sg.modelData.state === "done" ? theme.fg2 : theme.fg3 }
                                    Text { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: sg.modelData.right; color: sg.modelData.state === "cur" ? theme.fg : theme.fg3; font.pointSize: UiState.pt(12.5) } } }
                            Item { width: 1; height: 8 }
                            Item { width: parent.width; height: 40
                                Text { x: 2; text: root.v.runLine; color: theme.fg2; font.pointSize: UiState.pt(12.5) }
                                Text { anchors.right: parent.right; anchors.rightMargin: 2; text: root.v.runEta; color: theme.fg2; font.pointSize: UiState.pt(12.5) }
                                Rectangle { y: 24; x: 2; width: parent.width - 4; height: 5; radius: 2.5; color: theme.s3
                                    Rectangle { width: parent.width * root.v.runPct / 100; height: 5; radius: 2.5; gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: theme.accent } GradientStop { position: 1; color: "#d58ae8" } } } } }
                            Item { width: 1; height: 4 }
                            Repeater { model: root.v.notes.concat(["You can keep browsing while this runs. Results are saved with the folder, so closing the window does not stop it."])
                                delegate: Card { required property string modelData; required property int index; width: run.width; radius: 11; height: nt.implicitHeight + 20
                                    Icon { x: 12; y: 10; size: 15; strokeWidth: 1.8; color: theme.fg3; name: index === root.v.notes.length ? "check" : ""; path: index === root.v.notes.length ? "" : "M12 8v5M12 16.5v.01M12 21a9 9 0 100-18 9 9 0 000 18z" }
                                    Text { id: nt; x: 37; y: 10; width: parent.width - 49; text: modelData; wrapMode: Text.WordWrap; color: theme.fg2; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight } } }
                        }
                        // -------- review --------
                        Column {
                            id: rev
                            visible: root.page === "rev"; width: parent.width; spacing: 0
                            Row { spacing: 8
                                Repeater { model: root.v.tiles
                                    delegate: Card { required property var modelData; width: (rev.width - 8 * (root.v.tiles.length - 1)) / root.v.tiles.length; height: 64
                                        Column { x: 13; y: 11; spacing: 3
                                            Text { text: modelData.n; color: theme.fg; font.pixelSize: 21; font.weight: Font.DemiBold; font.letterSpacing: -0.4 }
                                            Text { text: modelData.l; color: theme.fg3; font.pixelSize: 12 } } } } }
                            Item { width: 1; height: 14 }
                            Flow { id: chipFlow; width: parent.width; spacing: 6
                                Repeater { model: root.v.chips
                                    delegate: Rectangle { id: chip; required property var modelData; height: 28; width: crow.implicitWidth + 22; radius: 14; color: modelData.on ? theme.s3 : (chh.hovered ? theme.s2 : theme.s1); border.width: modelData.on ? 1.5 : 1; border.color: modelData.on ? theme.accent : theme.lineSoft
                                        Row { id: crow; x: 11; anchors.verticalCenter: parent.verticalCenter; spacing: 7
                                            Dot { visible: chip.modelData.dot; c: chip.modelData.color; anchors.verticalCenter: parent.verticalCenter }
                                            Text { text: chip.modelData.label; color: chip.modelData.on ? theme.fg : theme.fg2; font.pointSize: UiState.pt(12.5); anchors.verticalCenter: parent.verticalCenter }
                                            Text { text: chip.modelData.n; color: theme.fg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter } }
                                        HoverHandler { id: chh; cursorShape: Qt.PointingHandCursor }
                                        TapHandler { onTapped: cull.setFilter(chip.modelData.key) }
                                        Accessible.role: Accessible.Button; Accessible.name: chip.modelData.label } } }
                            Item { width: 1; height: 12 }
                            // the stacks scroll in a list of their own that only builds the cards on screen (hundreds of stacks, each with its pictures, built at once made scrolling crawl and
                            // queued every thumbnail ahead of the one you picked); the detail card stays put beside it
                            Item { id: split; width: parent.width; height: Math.max(320, body.height - 18 - 64 - 14 - chipFlow.height - 12)
                                ListView { id: stk; width: parent.width - 262 - 16; height: parent.height; spacing: 8; clip: true; cacheBuffer: 500; boundsBehavior: Flickable.StopAtBounds; model: root.v.stacks
                                        delegate: Card { id: sk; required property var modelData; width: stk.width; height: 34 + fl.height + 11
                                            Row { x: 13; y: 8; spacing: 8; height: 20
                                                Text { text: sk.modelData.title; color: theme.fg; font.pixelSize: 12; font.weight: Font.DemiBold; anchors.verticalCenter: parent.verticalCenter }
                                                Text { text: sk.modelData.meta; color: theme.fg3; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter } }
                                            Flow { id: fl; x: 10; y: 34; width: parent.width - 20; spacing: 6
                                                Repeater { model: sk.modelData.frames
                                                    delegate: Item { id: fr; required property var modelData; width: 112; height: 74.7
                                                        RoundClip { anchors.fill: parent; radius: 8
                                                            Rectangle { anchors.fill: parent; color: theme.s3 }
                                                            ThumbImage { anchors.fill: parent; path: fr.modelData.path; bucket: 240; imageOpacity: fr.modelData.flagged ? 0.4 : 1; desaturate: fr.modelData.flagged ? 0.7 : 0 } }
                                                        Rectangle { visible: fr.modelData.sel; anchors.fill: parent; radius: 8; color: "transparent"; border.width: 2; border.color: theme.accent }
                                                        Rectangle { visible: fr.modelData.flagged; x: 5; y: 74.7 - 24; height: 19; width: bt.implicitWidth + 22; radius: 9.5; color: Qt.rgba(10 / 255, 12 / 255, 16 / 255, 0.72)
                                                            Row { x: 6; anchors.verticalCenter: parent.verticalCenter; spacing: 5
                                                                Dot { c: fr.modelData.color; width: 7; height: 7; radius: 3.5; anchors.verticalCenter: parent.verticalCenter }
                                                                Text { id: bt; text: fr.modelData.badge; color: "white"; font.pointSize: UiState.pt(10.5); font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter } } }
                                                        Rectangle { visible: fr.modelData.rank !== ""; x: 112 - 5 - width; y: 5; height: 19; width: rk.implicitWidth + 14; radius: 9.5; color: theme.accent
                                                            Text { id: rk; anchors.centerIn: parent; text: fr.modelData.rank; color: theme.onAccent; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold } }
                                                        Rectangle { visible: fr.modelData.restored; x: 112 - 5 - width; y: 5; height: 19; width: rs.implicitWidth + 14; radius: 9.5; color: Qt.rgba(10 / 255, 12 / 255, 16 / 255, 0.72)
                                                            Text { id: rs; anchors.centerIn: parent; text: "Restored"; color: "white"; font.pointSize: UiState.pt(10.5); font.weight: Font.Medium } }
                                                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                                                        TapHandler { onTapped: cull.pick(fr.modelData.path) }
                                                        Accessible.role: Accessible.Button; Accessible.name: fr.modelData.name + (fr.modelData.flagged ? ", flagged " + fr.modelData.badge : "") } } } }
                                    Text { visible: root.v.noRows; width: parent.width; topPadding: 26; horizontalAlignment: Text.AlignHCenter; text: "Nothing here. Every frame with this flag has been restored."; color: theme.fg3; font.pointSize: UiState.pt(12.5) } }
                                Card { id: detail; x: parent.width - 262; width: 262; height: dcol.implicitHeight + 24
                                    Column { id: dcol; x: 12; y: 12; width: parent.width - 24; spacing: 0
                                        Text { visible: !root.v.det.has; width: parent.width; topPadding: 26; bottomPadding: 26; horizontalAlignment: Text.AlignHCenter; text: "Pick a frame to see why it was flagged."; color: theme.fg3; font.pointSize: UiState.pt(12.5) }
                                        RoundClip { visible: root.v.det.has; width: parent.width; height: width * 2 / 3; radius: 9
                                            Rectangle { anchors.fill: parent; color: theme.s3 }
                                            ThumbImage { anchors.fill: parent; path: root.v.det.has ? root.v.det.path : ""; bucket: 540 } }
                                        Row { visible: root.v.det.has; topPadding: 10; spacing: 8
                                            Dot { visible: root.v.det.flagged; c: root.v.det.color; anchors.verticalCenter: parent.verticalCenter }
                                            Text { width: dcol.width - (root.v.det.flagged ? 16 : 0); text: root.v.det.title; elide: Text.ElideRight; color: theme.fg; font.pixelSize: 13; font.weight: Font.DemiBold } }
                                        Text { visible: root.v.det.has; width: parent.width; topPadding: 6; text: root.v.det.why; wrapMode: Text.WordWrap; color: theme.fg2; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
                                        Item { visible: root.v.det.has; width: 1; height: 10 }
                                        Rectangle { visible: root.v.det.has; width: parent.width; height: 1; color: theme.lineSoft }
                                        Column { visible: root.v.det.has; width: parent.width; topPadding: 10; spacing: 4
                                            Repeater { model: root.v.det.has ? root.v.det.rows : []
                                                delegate: Item { required property var modelData; width: dcol.width; height: 17
                                                    Text { text: modelData.k; color: theme.fg3; font.pixelSize: 12 }
                                                    Text { anchors.right: parent.right; text: modelData.v; color: theme.fg; font.pixelSize: 12 } } } }
                                        Row { visible: root.v.det.has; topPadding: 12; spacing: 6
                                            SButton { visible: root.v.det.canRestore; text: root.v.det.restoreLabel; h: 28; hpad: 11; radius: 9; onClicked: cull.toggleRestore() }
                                            SButton { text: "Show in grid"; h: 28; hpad: 11; radius: 9; onClicked: { var r = cull.rowOf(root.v.det.path); cull.hide(); if (r >= 0) UiState.requestRow = r } } } } }
                            }
                        }
                        // -------- apply --------
                        Column {
                            id: apply
                            visible: root.page === "apply"; width: parent.width; spacing: 7
                            Repeater { model: root.v.opts
                                delegate: Rectangle { id: op; required property var modelData; width: apply.width; height: oc.implicitHeight + 28; radius: 13
                                    color: modelData.on ? Qt.tint(theme.base, Qt.alpha(theme.accent, 0.10)) : theme.base; border.width: modelData.on ? 1.5 : 1; border.color: modelData.on ? Qt.alpha(theme.accent, 0.70) : (oh.hovered ? theme.line : theme.lineSoft)
                                    Rectangle { x: 14; y: 14; width: 16; height: 16; radius: 8; color: "transparent"; border.width: op.modelData.on ? 5 : 1.6; border.color: op.modelData.on ? theme.accent : theme.fg3 }
                                    Column { id: oc; x: 43; y: 13; width: parent.width - 57; spacing: 4
                                        Row { spacing: 8; Text { text: op.modelData.label; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                            Rectangle { visible: op.modelData.rec; height: 17; width: sg2.implicitWidth + 14; radius: 8.5; color: Qt.alpha(theme.accent, 0.14); anchors.verticalCenter: parent.verticalCenter
                                                Text { id: sg2; anchors.centerIn: parent; text: "Suggested"; color: theme.accentFg; font.pointSize: UiState.pt(11); font.weight: Font.Medium } } }
                                        Text { width: parent.width; text: op.modelData.desc; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight } }
                                    HoverHandler { id: oh; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: cull.setAct(op.modelData.key) }
                                    Accessible.role: Accessible.RadioButton; Accessible.name: modelData.label; Accessible.checked: modelData.on } }
                            Item { width: 1; height: 9 }
                            H3 { title: "FRAMES AFFECTED · " + root.v.applyCount; width: parent.width }
                            Item { width: 1; height: 2 }
                            Repeater { model: root.v.groups
                                delegate: Card { id: gp; required property var modelData; width: apply.width; height: 54; radius: 12; opacity: modelData.on ? 1 : 0.55
                                    Rectangle { x: 12; anchors.verticalCenter: parent.verticalCenter; width: 20; height: 20; radius: 6; color: gp.modelData.on ? theme.accent : theme.s3; border.color: gp.modelData.on ? "transparent" : theme.line
                                        Icon { visible: gp.modelData.on; anchors.centerIn: parent; name: "check"; size: 12; strokeWidth: 3; color: theme.onAccent } }
                                    Column { x: 44; anchors.verticalCenter: parent.verticalCenter
                                        Row { spacing: 8; Dot { c: gp.modelData.color; anchors.verticalCenter: parent.verticalCenter } Text { text: gp.modelData.label + " · " + gp.modelData.n; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium } }
                                        Text { text: gp.modelData.why; color: theme.fg3; font.pixelSize: 12 } }
                                    Item { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; height: 30; width: 44 + 34 * Math.max(0, gp.modelData.thumbs.length - 1)
                                        Repeater { model: gp.modelData.thumbs
                                            delegate: Item { required property int index; required property string modelData; x: index * 34; width: 44; height: 30
                                                Rectangle { x: -2; y: -2; width: 48; height: 34; radius: 8; color: theme.s1 }
                                                RoundClip { anchors.fill: parent; radius: 6; opacity: 0.7; Rectangle { anchors.fill: parent; color: theme.s3 } ThumbImage { anchors.fill: parent; path: modelData; bucket: 160 } } } } }
                                    TapHandler { onTapped: cull.toggleInc(gp.modelData.key) }
                                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                                    Accessible.role: Accessible.CheckBox; Accessible.checked: modelData.on; Accessible.name: modelData.label } }
                            Text { width: parent.width; topPadding: 8; text: "Frames you marked as winners are never touched. Near-duplicates are only suggestions, so they stay off until you turn them on."; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
                        }
                        // -------- done --------
                        Column {
                            id: done
                            visible: root.page === "done"; width: parent.width; topPadding: 46
                            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; width: 58; height: 58; radius: 29; color: Qt.tint(theme.s2, Qt.alpha(theme.win, 0.20)); border.color: Qt.alpha(theme.win, 0.4)
                                Icon { anchors.centerIn: parent; name: "check"; size: 28; strokeWidth: 2.4; color: theme.win } }
                            Text { width: parent.width; topPadding: 16; horizontalAlignment: Text.AlignHCenter; text: root.v.doneTitle; color: theme.fg; font.pixelSize: 17; font.weight: Font.DemiBold; font.letterSpacing: -0.17 }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; width: Math.min(420, parent.width); topPadding: 4; horizontalAlignment: Text.AlignHCenter; text: root.v.doneBody; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 19.4; lineHeightMode: Text.FixedHeight }
                        }
                    }
                }
                // ---- footer ----
                Rectangle {
                    y: parent.height - 60; width: parent.width; height: 60; color: theme.s1
                    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 28; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 28 - btns.width - 36; wrapMode: Text.WordWrap; maximumLineCount: 2; color: theme.fg3; font.pointSize: UiState.pt(12.5)
                           text: root.page === "prep" ? root.v.estimate : root.page === "run" ? "Frames are only read, never changed." : root.page === "rev" ? root.v.revNote : root.page === "apply" ? root.v.applyNote : "" }
                    Row { id: btns; anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                        SButton { visible: root.page === "prep"; text: "Cancel"; h: 34; hpad: 16; radius: 12; onClicked: cull.close() }
                        SButton { visible: root.page === "prep"; text: "Start analysis"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: cull.start() }
                        SButton { visible: root.page === "run"; text: "Keep browsing"; h: 34; hpad: 16; radius: 12; onClicked: cull.hide() }
                        SButton { visible: root.page === "run"; text: "Cancel analysis"; h: 34; hpad: 16; radius: 12; onClicked: cull.cancelRun() }
                        SButton { visible: root.page === "rev"; text: "Run again…"; h: 34; hpad: 16; radius: 12; onClicked: cull.rerun() }
                        SButton { visible: root.page === "rev"; text: "Close"; h: 34; hpad: 16; radius: 12; onClicked: cull.close() }
                        SButton { visible: root.page === "rev"; text: "Apply flags…   " + root.v.applyCount; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: cull.toApply() }
                        SButton { visible: root.page === "apply"; text: "Back"; h: 34; hpad: 16; radius: 12; onClicked: cull.toReview() }
                        SButton { visible: root.page === "apply"; text: root.v.applyLabel; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: cull.apply() }
                        SButton { visible: root.page === "done" && root.v.canUndo; text: "Undo"; h: 34; hpad: 16; radius: 12; onClicked: cull.undo() }
                        SButton { visible: root.page === "done"; text: "Back to results"; h: 34; hpad: 16; radius: 12; onClicked: cull.toReview() }
                        SButton { visible: root.page === "done"; text: "Done"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: cull.close() } }
                }
            }
        }
    }
}
