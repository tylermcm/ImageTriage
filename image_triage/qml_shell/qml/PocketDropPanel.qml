import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Shapes

// The PocketDrop page of the side panel. Everything it shows comes from `pocketDrop` (pocketdrop_model.py): PocketDrop's own engine
// state, or a scripted sample when the native module is not built. Layout, type and colours follow the HTML mockup's .pdx-* rules.
Item {
    id: page
    readonly property bool on: UiState.tab === "drop"
    readonly property var d: pocketDrop.st
    readonly property bool ready: d.card !== undefined
    readonly property string card: dropArea.containsDrag || UiState.pdDragging ? "drag" : (d.card || "busy")
    readonly property real innerW: width - 32
    readonly property string mono: Qt.platform.os === "windows" ? "Consolas" : "monospace"
    readonly property color cyan: "#43cae7"
    readonly property color recvGreen: "#7bcba1"
    readonly property var modeNames: ["Same Wi-Fi", "Anywhere", "Offline"]
    opacity: on ? 1 : 0
    visible: opacity > 0.01
    transform: Translate { y: page.on ? 0 : 8; Behavior on y { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } } }
    Behavior on opacity { NumberAnimation { duration: 250 } }
    onOnChanged: { if (on) { pocketDrop.start(); pocketDrop.refreshLibrary(UiState.selRow) } }
    Component.onCompleted: { if (on) pocketDrop.start() }

    // ---- small parts ------------------------------------------------------------------------------------------------------
    component PdButton: Item {
        id: b
        property string label
        property string iconPath: ""
        property bool primary: false
        property bool lifted: false
        signal clicked()
        implicitHeight: 34; height: 34
        scale: tap.pressed ? 0.97 : 1
        Behavior on scale { SpringAnimation { spring: 6; damping: 0.4; epsilon: 0.001 } }
        Rectangle {
            anchors.fill: parent; radius: 12
            color: b.primary ? (hov.hovered ? Qt.tint(theme.accent, Qt.alpha(theme.lift, 0.10)) : theme.accent)
                             : (hov.hovered || b.lifted ? Qt.tint(theme.s3, Qt.alpha(theme.lift, 0.06)) : theme.s3)
            border.color: b.primary ? "transparent" : theme.lineSoft
        }
        Row {
            anchors.centerIn: parent; spacing: 7
            Icon { visible: b.iconPath !== ""; path: b.iconPath; size: 15; strokeWidth: 1.9; color: b.primary ? theme.onAccent : theme.fg; anchors.verticalCenter: parent.verticalCenter }
            Text { text: b.label; color: b.primary ? theme.onAccent : theme.fg; font.pixelSize: 13; font.weight: b.primary ? Font.Medium : Font.Normal; anchors.verticalCenter: parent.verticalCenter }
        }
        HoverHandler { id: hov; cursorShape: Qt.PointingHandCursor }
        TapHandler { id: tap; onTapped: b.clicked() }
        Accessible.role: Accessible.Button; Accessible.name: b.label
    }

    component Chip: Item {
        id: c
        property string label
        property string iconPath
        property bool done: false
        signal clicked()
        width: row.width + 20; height: 30
        Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(c.done ? theme.win : theme.accent, hov.hovered && !c.done ? 0.26 : 0.16) }
        Row {
            id: row; anchors.centerIn: parent; spacing: 6
            Icon { path: c.done ? "M4.5 12.5l4.5 4.5 10.5-10.5" : c.iconPath; size: 14; strokeWidth: 2; color: c.done ? theme.win : theme.accentFg; anchors.verticalCenter: parent.verticalCenter }
            Text { text: c.label; color: c.done ? theme.win : theme.accentFg; font.pixelSize: 12; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter }
        }
        HoverHandler { id: hov; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: c.clicked() }
        Accessible.role: Accessible.Button; Accessible.name: c.label
    }

    // ---- header row: status pill and the menu -------------------------------------------------------------------------------
    Item {
        id: top
        x: 16; y: 12; width: page.innerW; height: 28
        Rectangle {
            id: pill
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(pillRow.width + 22, parent.width - 44); height: 26; radius: 13; color: theme.s1; border.color: theme.lineSoft
            readonly property color tone: ({ ok: theme.win, send: theme.accent, recv: page.cyan, warn: theme.rev, bad: theme.rej })[page.d.tone] || theme.fg3
            Row {
                id: pillRow; x: 11; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                Item { width: 7; height: 7; anchors.verticalCenter: parent.verticalCenter
                    Rectangle { anchors.centerIn: parent; width: 13; height: 13; radius: 7; color: Qt.alpha(pill.tone, 0.22); visible: page.d.tone !== "idle" }
                    Rectangle { anchors.fill: parent; radius: 4; color: pill.tone } }
                Text { id: pillText; text: page.d.status || ""; color: theme.fg2; font.pixelSize: 12; elide: Text.ElideRight; width: Math.min(implicitWidth, pill.parent.width - 44 - 41); anchors.verticalCenter: parent.verticalCenter }
            }
        }
        Item {
            id: menuBtn
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 28; height: 28
            Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, hov.hovered || menu.opened ? 0.08 : 0) }
            Icon { anchors.centerIn: parent; path: "M5 12h.01M12 12h.01M19 12h.01"; size: 18; strokeWidth: 2.4; color: hov.hovered || menu.opened ? theme.fg : theme.fg2 }
            HoverHandler { id: hov; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: UiState.setMenu("pdmenu") }
            Accessible.role: Accessible.Button; Accessible.name: "PocketDrop menu"
            PocketDropMenu { id: menu; panel: page }
        }
    }

    // ---- mode switch ----------------------------------------------------------------------------------------------------------
    Rectangle {
        id: seg
        x: 16; y: top.y + top.height + 10; width: page.innerW; height: 34; radius: 12
        color: Qt.alpha(theme.base, 0.6); border.color: theme.lineSoft
        readonly property real cell: (width - 4) / 3
        Rectangle {
            x: 2 + seg.cell * (page.d.mode || 0); y: 2; width: seg.cell; height: 30; radius: 10; color: theme.s3
            Behavior on x { SpringAnimation { spring: 3.4; damping: 0.22; epsilon: 0.25 } }
            Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.color: Qt.alpha(theme.lift, 0.10); anchors.topMargin: 0; anchors.bottomMargin: 1 }
        }
        Repeater {
            model: 3
            delegate: Item {
                required property int index
                x: 2 + index * seg.cell; y: 2; width: seg.cell; height: 30
                readonly property bool sel: (page.d.mode || 0) === index
                readonly property string badge: (page.d.badges || ["", "", ""])[index] || ""
                Text { anchors.centerIn: parent; width: parent.width - 6; horizontalAlignment: Text.AlignHCenter; fontSizeMode: Text.HorizontalFit; minimumPixelSize: 10; text: page.modeNames[index]; color: sel ? theme.fg : theme.fg2; font.pointSize: UiState.pt(12.5); Behavior on color { ColorAnimation { duration: 200 } } }
                Rectangle { visible: badge !== ""; x: parent.width - 13; y: 3; width: 6; height: 6; radius: 3
                    color: badge === "ok" ? theme.win : badge === "warn" ? theme.rev : theme.rej }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: pocketDrop.setMode(index) }
                Accessible.role: Accessible.Button; Accessible.name: page.modeNames[index]; Accessible.checked: sel
            }
        }
    }

    Shadow { visible: page.card === "qr"; x: 16; y: cardBox.y; width: page.innerW; height: width; radius: 20; offsetA: 10; insetA: 14; blurA: 30; alphaA: 0.6 }

    // ---- the card: QR code, or what is missing before there is one -----------------------------------------------------------
    Rectangle {
        id: cardBox
        x: 16; y: seg.y + seg.height + 12; width: page.innerW; height: width; radius: 20; clip: true
        color: page.card === "qr" ? "white" : page.card === "drag" ? Qt.tint(theme.s1, Qt.alpha(theme.accent, 0.10)) : theme.s1
        border.color: page.card === "qr" || page.card === "drag" ? "transparent" : theme.lineSoft
        
        // QR code: the engine's modules as one path in module units, scaled to 84% of the card and drawn without smoothing so the
        // squares stay sharp for a camera
        Shape {
            id: qrShape
            visible: page.card === "qr" && page.d.qrSize > 0
            readonly property real side: parent.width * 0.84
            readonly property int n: Math.max(1, page.d.qrSize || 1)
            x: (parent.width - side) / 2; y: (parent.height - side) / 2; width: n; height: n
            scale: side / n; transformOrigin: Item.TopLeft
            antialiasing: false; preferredRendererType: Shape.GeometryRenderer
            ShapePath { strokeColor: "transparent"; fillColor: "#111116"; PathSvg { path: page.card === "qr" ? (page.d.qrPath || "") : "" } }
            Accessible.role: Accessible.Graphic; Accessible.name: "QR code for " + (page.d.linkText || "the share link")
        }
        Rectangle {   // drag over: dashed accent ring 3px in, 2px wide
            visible: page.card === "drag"; x: 3; y: 3; width: parent.width - 6; height: parent.height - 6; radius: 17; color: "transparent"
            Shape { anchors.fill: parent; layer.enabled: true; layer.samples: 4
                ShapePath { strokeColor: theme.accent; strokeWidth: 2; fillColor: "transparent"; strokeStyle: ShapePath.DashLine; dashPattern: [2.5, 3]
                    PathSvg { path: Icons.rect(1, 1, cardBox.width - 8, cardBox.height - 8, 16) } } }
        }

        // centred message for every state that is not a QR code
        Column {
            id: msg
            visible: page.card !== "qr"
            anchors.centerIn: parent; width: parent.width - 48; spacing: 6
            readonly property string c: page.card
            readonly property bool anywhere: (page.d.mode || 0) === 1
            Rectangle {
                visible: ["drag", "missing", "failed", "bluetooth", "nonet"].indexOf(msg.c) >= 0
                anchors.horizontalCenter: parent.horizontalCenter; width: 56; height: 56; radius: 28
                color: msg.c === "failed" ? Qt.alpha(theme.rej, 0.14) : msg.c === "drag" ? Qt.alpha(theme.accent, 0.16) : theme.s2
                Icon { anchors.centerIn: parent; size: 26; strokeWidth: 1.7
                    color: msg.c === "failed" ? theme.rej : msg.c === "drag" ? theme.accentFg : msg.c === "nonet" ? theme.fg3 : page.cyan
                    path: msg.c === "drag" ? "M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19h14" : msg.c === "missing" ? "M12 21a9 9 0 100-18 9 9 0 000 18zM3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"
                        : msg.c === "failed" ? "M6 6l12 12M18 6L6 18" : msg.c === "bluetooth" ? "M8 3h8a1 1 0 011 1v16a1 1 0 01-1 1H8a1 1 0 01-1-1V4a1 1 0 011-1zM11 18h2"
                        : "M2 9a15 15 0 0120 0M5 12.5a10 10 0 0114 0M8.5 16a5 5 0 017 0M12 19.5h.01" }
            }
            Item { visible: msg.c === "busy"; anchors.horizontalCenter: parent.horizontalCenter; width: 36; height: 42
                Item { width: 36; height: 36
                    Shape { anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
                        ShapePath { strokeColor: Qt.alpha(theme.accent, 0.22); strokeWidth: 3; fillColor: "transparent"; PathSvg { path: Icons.circle(18, 18, 16.5) } }
                        ShapePath { strokeColor: theme.accent; strokeWidth: 3; fillColor: "transparent"; capStyle: ShapePath.FlatCap
                            PathAngleArc { centerX: 18; centerY: 18; radiusX: 16.5; radiusY: 16.5; startAngle: -90; sweepAngle: 90 } } }
                    NumberAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: msg.c === "busy" && page.on }
                } }
            Text { visible: text !== ""; width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; lineHeight: 19.5; lineHeightMode: Text.FixedHeight
                text: ({ drag: "Drop to share", busy: msg.anywhere ? "Starting Anywhere tunnel…" : "Starting offline hotspot…", missing: "Share beyond your Wi-Fi",
                         downloading: "Downloading cloudflared…", failed: msg.anywhere ? "Couldn’t start the tunnel" : "Couldn’t start the hotspot",
                         bluetooth: "Connect your phone over Bluetooth", nonet: "Not connected to a network" })[msg.c] || "" }
            Text { visible: text !== ""; width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                color: theme.fg3; font.pixelSize: 12; lineHeight: 17.4; lineHeightMode: Text.FixedHeight
                text: ({ busy: msg.anywhere ? "Securing a private link to your computer." : "Windows is setting up a Wi-Fi network.",
                         missing: "A free Cloudflare tunnel lets your phone connect over mobile data. No account needed.",
                         failed: page.d.cardMsg || "", bluetooth: "Enable Bluetooth tethering or Personal Hotspot, then connect using Access point in Windows.",
                         nonet: "Create one directly from this computer." })[msg.c] || "" }
            Item { visible: msg.c === "downloading"; width: parent.width; height: 14
                Rectangle { y: 8; width: parent.width; height: 6; radius: 3; color: theme.s3
                    Rectangle { width: parent.width * (page.d.pct || 0) / 100; height: 6; radius: 3; color: theme.accent } } }
            Text { visible: msg.c === "downloading"; width: parent.width; horizontalAlignment: Text.AlignHCenter; text: (page.d.pct || 0) + "%"; color: theme.fg3; font.pixelSize: 12 }
            Item { visible: ["missing", "failed", "bluetooth", "nonet"].indexOf(msg.c) >= 0; width: parent.width; height: 44
                PdButton { y: 10; anchors.horizontalCenter: parent.horizontalCenter; width: btnW.implicitWidth + 28
                    primary: msg.c !== "failed"
                    label: ({ missing: "Enable \u00b7 one-time 40 MB", failed: "Try again", bluetooth: "Open Bluetooth setup", nonet: "Start offline hotspot" })[msg.c] || ""
                    onClicked: pocketDrop.command("card")
                    Text { id: btnW; visible: false; text: parent.label; font.pixelSize: 13; font.weight: Font.Medium } } }
        }
        // Items dragged in from outside (files from Explorer, text)
        DropArea {
            id: dropArea
            anchors.fill: parent
            keys: ["text/uri-list", "text/plain"]
            onDropped: (drop) => { if (drop.hasUrls) pocketDrop.addUrls(drop.urls); else if (drop.hasText) pocketDrop.addText(drop.text); drop.accept() }
        }
    }

    // ---- link row ------------------------------------------------------------------------------------------------------------
    Rectangle {
        id: linkRow
        x: 16; y: cardBox.y + cardBox.height + 10; width: page.innerW; height: 40; radius: 12; color: theme.s1; border.color: theme.lineSoft
        readonly property bool shareShown: !!page.d.hasLink && (page.d.mode || 0) === 1
        Text {
            x: 12; width: parent.width - 12 - 5 - (page.d.hasLink ? copyChip.width + 6 : 0) - (linkRow.shareShown ? shareChip.width + 6 : 0)
            anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight
            text: page.d.hasLink ? page.d.linkText : (page.d.linkEmpty || ""); color: page.d.hasLink ? theme.fg2 : theme.fg3
            font.family: page.d.hasLink ? page.mono : Qt.application.font.family; font.pixelSize: 12
        }
        Row {
            anchors.right: parent.right; anchors.rightMargin: 5; anchors.verticalCenter: parent.verticalCenter; spacing: 6
            Chip { id: shareChip; visible: linkRow.shareShown; label: "Share"; iconPath: "M12 15V4M8 8l4-4 4 4M5 14v4a2 2 0 002 2h10a2 2 0 002-2v-4"; onClicked: pocketDrop.command("share") }
            Chip { id: copyChip; visible: !!page.d.hasLink; done: !!page.d.copied; label: page.d.copied ? "Copied" : (page.d.mode === 1 ? "Copy QR" : "Copy")
                iconPath: "M9 9h10v11H9zM5 15V4h10"; onClicked: pocketDrop.command("copy") }
        }
    }

    // ---- one line: transfer, receive code, result, bundle size or a hint ----------------------------------------------------
    Item {
        id: infoRow
        x: 16; y: linkRow.y + linkRow.height + 10; width: page.innerW; height: 34
        readonly property var i: page.d.info || ({})
        readonly property string kind: i.kind || "hint"
        Column {
            visible: infoRow.kind === "active"; anchors.verticalCenter: parent.verticalCenter; width: parent.width
            Item { width: parent.width; height: 17
                Text { width: parent.width - rt.implicitWidth - 10; elide: Text.ElideRight; text: infoRow.i.left || ""; color: theme.fg; font.pixelSize: 12; font.weight: Font.Medium }
                Text { id: rt; anchors.right: parent.right; text: infoRow.i.right || ""; color: theme.fg3; font.pixelSize: 12 } }
            Item { width: parent.width; height: 9
                Rectangle { y: 6; width: parent.width; height: 3; radius: 1.5; color: theme.s3
                    Rectangle { width: parent.width * (infoRow.i.frac || 0); height: 3; radius: 1.5; color: infoRow.i.incoming ? page.cyan : theme.accent } } }
        }
        Row {
            visible: infoRow.kind === "code"; anchors.centerIn: parent; spacing: 8
            Text { text: "Receive code"; color: theme.fg3; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            Rectangle { width: codeT.implicitWidth + 16; height: 26; radius: 7; color: Qt.alpha(theme.accent, 0.14); anchors.verticalCenter: parent.verticalCenter
                Text { id: codeT; anchors.centerIn: parent; text: infoRow.i.code || ""; color: theme.accentFg; font.family: page.mono; font.pixelSize: 13; font.weight: Font.DemiBold; font.letterSpacing: 1.56 } }
        }
        Row {
            visible: infoRow.kind === "done"; anchors.centerIn: parent; spacing: 7
            Icon { path: "M4.5 12.5l4.5 4.5 10.5-10.5"; size: 15; strokeWidth: 2.2; color: theme.win; anchors.verticalCenter: parent.verticalCenter }
            Text { text: infoRow.i.left || ""; color: theme.win; font.pixelSize: 12; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter }
        }
        Item {
            visible: infoRow.kind === "sum"; anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 17
            Text { width: parent.width - sr.implicitWidth - 10; elide: Text.ElideRight; text: infoRow.i.left || ""; color: theme.fg3; font.pixelSize: 12 }
            Text { id: sr; anchors.right: parent.right; text: infoRow.i.right || ""; color: theme.fg3; font.pixelSize: 12 }
        }
        Text { visible: infoRow.kind === "hint"; anchors.centerIn: parent; width: parent.width; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight; text: infoRow.i.left || ""; color: theme.fg3; font.pixelSize: 12 }
    }

    // ---- bottom buttons ------------------------------------------------------------------------------------------------------
    Item {
        id: buttons
        x: 16; width: page.innerW; height: 34; y: page.height - 14 - height
        readonly property real unit: (width - 16) / 3.25
        PdButton { id: addBtn; width: buttons.unit * 1.25; label: "Add files"; iconPath: "M12 5v14M5 12h14"; primary: true; onClicked: filesDialog.open() }
        PdButton { id: libBtn; x: addBtn.width + 8; width: buttons.unit; label: "Library"; iconPath: "M12 4l8.5 4.5L12 13 3.5 8.5zM3.5 12.5L12 17l8.5-4.5"; lifted: library.opened
            onClicked: UiState.setMenu("pdlib") }
        PdButton { x: addBtn.width + libBtn.width + 16; width: buttons.unit; label: page.d.rows && page.d.rows.length || pocketDrop.rows.length ? "Clear" : "Paste"
            iconPath: pocketDrop.rows.length ? "M6 6l12 12M18 6L6 18" : "M9 4h6v3H9zM7 5.5H6a1 1 0 00-1 1V20a1 1 0 001 1h12a1 1 0 001-1V6.5a1 1 0 00-1-1h-1"
            onClicked: { if (pocketDrop.rows.length) pocketDrop.clearAll(); else pocketDrop.command("paste") } }
        PocketDropLibrary { id: library; panel: page; panelW: page.width }
    }

    // ---- shared and received items -------------------------------------------------------------------------------------------
    ListView {
        id: list
        x: 10; y: infoRow.y + infoRow.height + 4; width: page.width - 20; height: Math.max(0, buttons.y - 10 - y)
        clip: true; boundsBehavior: Flickable.StopAtBounds; model: pocketDrop.rows
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 4
            contentItem: Rectangle { implicitWidth: 3; radius: 1.5; color: theme.line }
            background: Item {} }
        Text { visible: list.count === 0; x: 12; width: parent.width - 24; y: 20; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; lineHeight: 18; lineHeightMode: Text.FixedHeight
            text: "Drop files here to share them with your phone.\nAnything your phone sends shows up here too."; color: theme.fg3; font.pixelSize: 12 }
        delegate: Item {
            id: row
            required property var modelData
            width: list.width; height: 46
            readonly property bool received: modelData.tone === "recv"
            readonly property bool isColl: modelData.kind === "coll"
            Rectangle { x: 0; width: parent.width; height: 46; radius: 12; color: theme.s1; opacity: rowHover.hovered ? 1 : 0 }
            // icon tile (or the stacked thumbnails of a library group)
            Item {
                x: 14; y: 8; width: row.isColl ? 54 : 30; height: 30
                Rectangle { visible: !row.isColl; anchors.fill: parent; radius: 9
                    color: row.received ? Qt.alpha(theme.win, 0.14) : modelData.kind === "note" ? Qt.alpha(theme.accent, 0.14) : theme.s3 }
                Icon { visible: !row.isColl; anchors.centerIn: parent; size: 16; strokeWidth: 1.7
                    color: row.received ? theme.win : modelData.kind === "note" ? theme.accentFg : theme.fg2
                    path: modelData.kind === "folder" ? "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"
                        : (modelData.kind === "note" || modelData.kind === "rnote") ? "M5 6h14M5 11h14M5 16h9" : "M7 3h7l5 5v12a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1zM14 3v5h5" }
                Rectangle { visible: row.received; x: 20; y: 20; width: 14; height: 14; radius: 7; color: theme.win; border.width: 2; border.color: rowHover.hovered ? theme.s1 : theme.s2
                    Icon { anchors.centerIn: parent; size: 9; strokeWidth: 3; color: "white"; path: "M12 5v12M6.5 12L12 17.5 17.5 12" } }
                Repeater {
                    model: row.isColl ? 3 : 0
                    delegate: Item {
                        required property int index
                        x: index * 16; width: 30; height: 30
                        Rectangle { anchors.fill: parent; radius: 8; color: rowHover.hovered ? theme.s1 : theme.s2 }
                        RoundClip { x: 2; y: 2; width: 26; height: 26; radius: 6
                            Rectangle { anchors.fill: parent; color: theme.s3 }
                            ThumbImage { anchors.fill: parent; bucket: 160; path: row.modelData.thumbs && row.modelData.thumbs[index] ? row.modelData.thumbs[index] : "" } }
                    }
                }
            }
            Column {
                x: 14 + (row.isColl ? 54 : 30) + 11; y: 6.5; width: parent.width - x - (row.isColl ? asChip.width + 14 : 40)
                Text { width: parent.width; height: 18; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; text: modelData.title; color: theme.fg; font.pixelSize: 13 }
                Text { width: parent.width; height: 15; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; text: modelData.sub; color: row.received ? page.recvGreen : theme.fg3; font.pointSize: UiState.pt(11.5) }
            }
            // "Send as" chip of a library group (gives way to the remove button on hover)
            Rectangle {
                id: asChip
                visible: row.isColl && !rowHover.hovered; anchors.right: parent.right; anchors.rightMargin: 6; y: 11; width: asRow.width + 16; height: 24; radius: 7; color: theme.s3
                Row { id: asRow; anchors.centerIn: parent; spacing: 4
                    Text { text: modelData.asLabel; color: asHover.hovered ? theme.fg : theme.fg2; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
                    Icon { path: "M7 10l5 5 5-5"; size: 11; strokeWidth: 2.2; color: asHover.hovered ? theme.fg : theme.fg2; anchors.verticalCenter: parent.verticalCenter } }
                HoverHandler { id: asHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: pocketDrop.toggleSendAs(row.modelData.key) }
                Accessible.role: Accessible.Button; Accessible.name: "Send as " + modelData.asLabel
            }
            Item {
                visible: rowHover.hovered; anchors.right: parent.right; anchors.rightMargin: 6; y: 9; width: 28; height: 28
                Rectangle { anchors.fill: parent; radius: 8; color: theme.s3; opacity: xHover.hovered ? 1 : 0 }
                Icon { anchors.centerIn: parent; path: "M6 6l12 12M18 6L6 18"; size: 14; strokeWidth: 1.7; color: xHover.hovered ? theme.fg : theme.fg3 }
                HoverHandler { id: xHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: pocketDrop.removeRow(row.modelData.key) }
                Accessible.role: Accessible.Button; Accessible.name: "Remove " + modelData.title
            }
            HoverHandler { id: rowHover; cursorShape: row.received ? Qt.PointingHandCursor : Qt.ArrowCursor }
            TapHandler { enabled: row.received; onTapped: pocketDrop.command("row", String(row.modelData.index)) }
        }
    }

    FileDialog { id: filesDialog; title: "Choose files to share"; fileMode: FileDialog.OpenFiles; onAccepted: pocketDrop.addUrls(selectedFiles) }
    FolderDialog { id: folderDialog; title: "Choose a folder to share"; onAccepted: pocketDrop.addUrls([selectedFolder]) }
    FolderDialog { id: inboxDialog; title: "Choose where received files are saved"; onAccepted: pocketDrop.changeInbox(selectedFolder) }
    function chooseFiles() { filesDialog.open() }
    function chooseFolder() { folderDialog.open() }
    function chooseInbox() { inboxDialog.open() }
}
