import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Shapes

// PocketDrop's one screen. It draws what `pocketDrop.st` (the engine's stateJson) says and answers every click with a named command,
// so the standalone app and Image Triage show exactly the same thing. See CONTRACT.md for what the host must provide.
Item {
    id: page
    property bool showTitle: true                 // the standalone window shows the name; Image Triage has its own header
    property bool autoStart: true                 // start the engine as soon as the view exists
    property bool externalDrag: false             // the host is dragging something toward the view
    property Component thumb: Component {         // how a collection's thumbnails are drawn; a host can supply its own cache
        Image { property string path; fillMode: Image.PreserveAspectCrop; asynchronous: true; source: path }
    }
    property bool handleRisk: true                // ask before Anywhere (hosts that ask themselves set this false)
    property alias extraButtons: extraRow.data    // hosts add buttons beside Add (Image Triage: Library)
    property alias menuExtras: menu.extras        // and entries in the menu's settings group
    readonly property var d: pocketDrop.st
    readonly property var pc: d.pc || ({})
    readonly property var nearby: pc.nearby || ({})
    readonly property var peers: nearby.peers || []
    property var sendTarget: null                 // the nearby PC a file picker is open for
    readonly property bool pcActive: (pc.mode || 0) !== 0
    readonly property string card: dropArea.containsDrag || externalDrag ? "drag" : (d.card || "busy")
    readonly property string mono: Qt.platform.os === "windows" ? "Consolas" : "monospace"
    readonly property color cyan: "#43cae7"
    readonly property real gutter: 16
    readonly property real innerW: width - 2 * gutter
    readonly property int mode: d.mode || 0
    readonly property real side: Math.max(150, Math.min(innerW, 300, height - 440))
    property alias menuPopup: menu
    property bool showUrl: false
    property bool showCode: false
    Component.onCompleted: { if (autoStart) pocketDrop.start() }

    // The file dialogs are made when they are asked for and dropped when closed: building four at startup costs time nobody needs.
    function openDialog(component) {
        var dialog = component.createObject(page)
        dialog.accepted.connect(function() { dialog.destroy(1000) })
        dialog.rejected.connect(function() { dialog.destroy(1000) })
        dialog.open()
    }
    function chooseFiles() { openDialog(filesDialog) }
    function chooseFolder() { openDialog(folderDialog) }
    function chooseInbox() { openDialog(inboxDialog) }
    // Files go to a nearby PC: put them in the list, then aim the share at that PC.
    function sendTo(peer, urls) { if (urls && urls.length) pocketDrop.addUrls(urls); pocketDrop.command("pc_send_to", peer.id) }
    function pickFor(peer) { if (pocketDrop.rows.length) sendTo(peer, []); else { sendTarget = peer; openDialog(sendDialog) } }
    function hostOf(link) { return (link || "").split("/")[0] }

    // ---- header ------------------------------------------------------------------------------------------------------------
    Item {
        id: top
        x: page.gutter; y: 12; width: page.innerW; height: 30
        Row {
            spacing: 10; anchors.verticalCenter: parent.verticalCenter
            Rectangle { visible: page.showTitle; width: 28; height: 28; radius: 9; anchors.verticalCenter: parent.verticalCenter
                gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: "#8b7cff" } GradientStop { position: 1; color: "#4fc3f7" } }
                PdIcon { anchors.centerIn: parent; size: 16; strokeWidth: 2.3; color: "white"; path: "M12 4v11M7 10.5l5 5 5-5M5 20h14" } }
            Text { visible: page.showTitle; text: "PocketDrop"; color: theme.fg; font.pixelSize: 16; font.weight: Font.DemiBold; anchors.verticalCenter: parent.verticalCenter }
            Rectangle {
                id: pill
                anchors.verticalCenter: parent.verticalCenter
                width: Math.min(pillProbe.implicitWidth + 41, page.innerW - (page.showTitle ? 160 : 44)); height: 26; radius: 13; color: theme.s1; border.color: theme.lineSoft
                readonly property color tone: ({ ok: theme.win, send: theme.accent, recv: page.cyan, warn: theme.rev, bad: theme.rej })[page.d.tone] || theme.fg3
                Row {
                    id: pillRow; x: 11; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    Item { width: 7; height: 7; anchors.verticalCenter: parent.verticalCenter
                        Rectangle { anchors.centerIn: parent; width: 13; height: 13; radius: 7; color: Qt.alpha(pill.tone, 0.22); visible: page.d.tone !== "idle" }
                        Rectangle { anchors.fill: parent; radius: 4; color: pill.tone } }
                    Text { id: pillText; text: page.d.status || ""; color: theme.fg2; font.pixelSize: 12; elide: Text.ElideRight; width: Math.min(pillProbe.implicitWidth, page.innerW - (page.showTitle ? 160 : 44) - 41); anchors.verticalCenter: parent.verticalCenter }
                    Text { id: pillProbe; visible: false; text: page.d.status || ""; font.pixelSize: 12 }
                }
            }
        }
        Item {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 30; height: 30
            Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, menuHover.hovered || menu.opened ? 0.08 : 0) }
            PdIcon { anchors.centerIn: parent; path: "M5 12h.01M12 12h.01M19 12h.01"; size: 18; strokeWidth: 2.6; color: menuHover.hovered || menu.opened ? theme.fg : theme.fg2 }
            HoverHandler { id: menuHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: menu.opened ? menu.close() : menu.open() }
            Accessible.role: Accessible.Button; Accessible.name: "PocketDrop menu"
        }
    }

    // ---- the card: QR code, or what is missing before there is one, or the PC transfer --------------------------------------
    Rectangle {
        id: cardBox
        x: (page.width - page.side) / 2; y: top.y + top.height + 12; width: page.side; height: page.side; radius: 20; clip: true
        color: page.card === "qr" && !page.pcActive ? "white" : page.card === "drag" ? Qt.tint(theme.s1, Qt.alpha(theme.accent, 0.10)) : theme.s1
        border.color: page.card === "qr" || page.card === "drag" ? "transparent" : theme.lineSoft

        Shape {
            id: qrShape
            visible: page.card === "qr" && !page.pcActive && page.d.qrSize > 0
            readonly property real edge: parent.width * 0.84
            readonly property int n: Math.max(1, page.d.qrSize || 1)
            x: (parent.width - edge) / 2; y: (parent.height - edge) / 2; width: n; height: n
            scale: edge / n; transformOrigin: Item.TopLeft
            antialiasing: false
            ShapePath { strokeColor: "transparent"; fillColor: "#111116"; PathSvg { path: page.card === "qr" && !page.pcActive ? (page.d.qrPath || "") : "" } }
            Accessible.role: Accessible.Graphic; Accessible.name: "QR code for " + (page.d.linkText || "the share link")
        }
        Rectangle {
            visible: page.card === "drag"; x: 3; y: 3; width: parent.width - 6; height: parent.height - 6; radius: 17; color: "transparent"
            border.color: theme.accent; border.width: 2
        }

        // every state that is not a QR code
        Column {
            id: msg
            visible: page.card !== "qr" || page.pcActive
            anchors.centerIn: parent; width: parent.width - 40; spacing: 6
            readonly property string c: page.pcActive && page.card !== "drag" ? "pc" : page.card
            readonly property bool anywhere: page.mode === 1
            // PC Share, in words
            readonly property bool sending: page.pc.mode === 1
            readonly property int phase: page.pc.phase || 0
            readonly property var senders: page.pc.senders || []
            readonly property string pcTitle: {
                if (sending) {
                    if (page.pc.pair && page.pc.pair.pending) return "Code " + page.pc.pair.code
                    if (page.pc.sent) return "Sent successfully"
                    if (page.nearby.target) return "Waiting for " + page.nearby.target
                    if (!pocketDrop.rows.length) return "Choose files to send"
                    return "Waiting for the other PC"
                }
                if (phase === 7) return "Code " + (page.pc.code || "")
                if (phase === 2) return "Receiving…"
                if (phase === 3) return "Checking the file…"
                if (phase === 4) return "Received successfully"
                if (phase === 5) return "Transfer interrupted"
                if (phase === 6) return "Transfer cancelled"
                if (phase === 1) return "Connecting…"
                return senders.length > 1 ? "Choose a sending PC" : "Looking for a sender…"
            }
            readonly property string pcDetail: {
                if (sending) {
                    if (page.pc.pair && page.pc.pair.pending) return page.pc.pair.name + " wants to receive"
                    if (page.pc.sent) return "The transfer is closed"
                    if (page.nearby.target) return "They need to accept on their screen"
                    return pocketDrop.rows.length ? "On the other PC choose Receive files from another PC" : "Drop files here or choose Add"
                }
                if (phase === 7) return "Check it matches on the sending PC, then wait for it to accept"
                if (phase === 2 && page.pc.total) return Math.round(100 * (page.pc.received || 0) / page.pc.total) + "%"
                if (phase === 5 || phase === 6) return page.pc.error || ""
                if (phase === 0) return "On the other PC choose Send files to another PC"
                return page.pc.name || ""
            }
            readonly property string pcAction: {
                if (sending) return page.pc.sent ? "Send again" : pocketDrop.rows.length ? "Stop sharing" : "Choose files"
                if (phase === 4) return "Show in folder"
                if (phase === 5 || phase === 6) return "Try again"
                return "Cancel"
            }
            function runPc() {
                if (sending) {
                    if (page.pc.sent) pocketDrop.command("pc_send")
                    else if (!pocketDrop.rows.length) page.chooseFiles()
                    else pocketDrop.command("pc_stop")
                } else if (phase === 4) pocketDrop.command("pc_action")
                else if (phase === 5 || phase === 6) pocketDrop.command("pc_receive")
                else pocketDrop.command("pc_stop")
            }

            Rectangle {
                visible: ["drag", "missing", "failed", "bluetooth", "nonet", "pc"].indexOf(msg.c) >= 0
                anchors.horizontalCenter: parent.horizontalCenter; width: 52; height: 52; radius: 26
                color: msg.c === "failed" ? Qt.alpha(theme.rej, 0.14) : msg.c === "drag" ? Qt.alpha(theme.accent, 0.16) : theme.s2
                PdIcon { anchors.centerIn: parent; size: 26; strokeWidth: 1.7
                    color: msg.c === "failed" ? theme.rej : msg.c === "drag" ? theme.accentFg : msg.c === "nonet" ? theme.fg3 : page.cyan
                    path: msg.c === "drag" ? "M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19h14" : msg.c === "missing" ? "M12 21a9 9 0 1 0 0 -18 9 9 0 000 18zM3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"
                        : msg.c === "failed" ? "M6 6l12 12M18 6L6 18" : msg.c === "bluetooth" ? "M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1 -1 1H8a1 1 0 0 1 -1 -1V4a1 1 0 0 1 1 -1zM11 18h2"
                        : msg.c === "pc" ? "M4 5h16v10H4zM9 19h6M12 15v4" : "M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01" }
            }
            BusyIndicator { visible: msg.c === "busy"; running: visible; anchors.horizontalCenter: parent.horizontalCenter; width: 40; height: 40
                palette.dark: theme.accent }
            Text { visible: text !== ""; width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold
                text: msg.c === "pc" ? msg.pcTitle : ({ drag: "Drop to share", busy: msg.anywhere ? "Starting Anywhere tunnel…" : "Starting offline hotspot…", missing: "Share beyond your Wi-Fi",
                         downloading: "Downloading cloudflared…", failed: msg.anywhere ? "Couldn’t start the tunnel" : "Couldn’t start the hotspot",
                         bluetooth: "Connect your phone over Bluetooth", nonet: "Not connected to a network" })[msg.c] || "" }
            Text { visible: text !== ""; width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                color: theme.fg3; font.pixelSize: 12
                text: msg.c === "pc" ? msg.pcDetail : ({ busy: msg.anywhere ? "Securing a private link to your computer." : "Setting up a Wi-Fi network.",
                         missing: "A free Cloudflare tunnel lets your phone connect over mobile data. No account needed.",
                         failed: page.d.cardMsg || "", bluetooth: "Enable Bluetooth tethering or Personal Hotspot, then connect your phone.",
                         nonet: "Create a network directly from this computer." })[msg.c] || "" }
            Item { visible: msg.c === "downloading" || (msg.c === "pc" && !msg.sending && msg.phase === 2); width: parent.width; height: 14
                Rectangle { y: 8; width: parent.width; height: 6; radius: 3; color: theme.s3
                    Rectangle { height: 6; radius: 3; color: theme.accent
                        width: parent.width * (msg.c === "pc" ? (page.pc.total ? (page.pc.received || 0) / page.pc.total : 0) : (page.d.pct || 0) / 100) } } }
            Column { visible: msg.c === "pc" && !msg.sending && msg.phase === 0 && msg.senders.length > 1; width: parent.width; spacing: 6
                Repeater { model: msg.senders
                    delegate: PdButton { required property var modelData; required property int index; width: parent.width; label: modelData.name + "  ·  " + modelData.ip
                        onClicked: pocketDrop.command("pc_pick", String(index)) } } }
            Item { visible: ["missing", "failed", "bluetooth", "nonet", "pc"].indexOf(msg.c) >= 0 && !(msg.c === "pc" && msg.phase === 0 && !msg.sending && msg.senders.length > 1); width: parent.width; height: 46
                PdButton { y: 10; anchors.horizontalCenter: parent.horizontalCenter; width: Math.max(130, implicitWidth); primary: msg.c !== "failed"
                    label: msg.c === "pc" ? msg.pcAction : ({ missing: "Enable · one-time 40 MB", failed: "Try again", bluetooth: "Open Bluetooth setup", nonet: "Start offline hotspot" })[msg.c] || ""
                    onClicked: { if (msg.c === "pc") msg.runPc(); else pocketDrop.command("card") } } }
        }
        Accessible.role: Accessible.Pane; Accessible.name: "Share"
    }

    // ---- what the link is, and how to take it ----------------------------------------------------------------------------------
    Item {
        id: linkRow
        x: page.gutter; y: cardBox.y + cardBox.height + 10; width: page.innerW; height: 34
        visible: !page.pcActive
        Column {
            anchors.verticalCenter: parent.verticalCenter; width: parent.width - actions.width - 8
            Text { width: parent.width; elide: Text.ElideRight; font.pixelSize: 13; font.weight: page.d.hasLink ? Font.Medium : Font.Normal
                color: page.d.hasLink ? theme.fg : theme.fg3
                text: page.d.hasLink ? (page.mode === 1 ? "Anywhere" : page.mode === 2 ? "Offline hotspot" : "Same Wi-Fi") : (page.d.linkEmpty || "") }
            Text { visible: !!page.d.hasLink; width: parent.width; elide: Text.ElideMiddle; color: theme.fg3; font.pixelSize: 11
                font.family: page.showUrl ? page.mono : Qt.application.font.family
                text: page.showUrl ? (page.d.linkText || "") : page.mode === 1 ? "Scan with your phone" : "Scan with your phone  ·  " + page.hostOf(page.d.linkText)
                TapHandler { onTapped: page.showUrl = !page.showUrl }
                Accessible.role: Accessible.Button; Accessible.name: "Show the full link" }
        }
        Row {
            id: actions
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 6
            PdChip { visible: !!page.d.hasLink && page.mode === 1; label: "Share"; iconPath: "M12 15V4M8 8l4-4 4 4M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-4"; onClicked: pocketDrop.command("share") }
            PdChip { objectName: "copyChip"; visible: !!page.d.hasLink; done: !!page.d.copied; label: page.d.copied ? "Copied" : (page.mode === 1 ? "Copy QR" : "Copy link")
                iconPath: "M9 9h10v11H9zM5 15V4h10"; onClicked: pocketDrop.command("copy") }
        }
    }

    // ---- other PCs nearby: drop files on one to send them ------------------------------------------------------------------------
    Item {
        id: nearbyRow
        objectName: "nearbyStrip"
        x: page.gutter; y: linkRow.y + linkRow.height + 4; width: page.innerW
        height: visible ? 40 : 0
        visible: !page.pcActive && page.peers.length > 0
        Text { id: nearbyLabel; anchors.verticalCenter: parent.verticalCenter; text: "Nearby"; color: theme.fg3; font.pixelSize: 11; font.weight: Font.Medium; font.letterSpacing: 0.5 }
        Flickable {
            x: nearbyLabel.width + 10; width: parent.width - x; height: parent.height
            contentWidth: peerRow.width; clip: true; boundsBehavior: Flickable.StopAtBounds
            Row {
                id: peerRow; spacing: 6; y: 5
                Repeater {
                    model: page.peers
                    delegate: Item {
                        id: peerChip
                        objectName: "peerChip" + index
                        required property var modelData
                        required property int index
                        width: chipRow.width + 20; height: 30
                        readonly property bool aimed: drop.containsDrag
                        Rectangle { anchors.fill: parent; radius: 10; color: peerChip.aimed ? Qt.alpha(theme.accent, 0.28) : Qt.alpha(theme.accent, ph.hovered ? 0.2 : 0.12)
                            border.color: peerChip.aimed ? theme.accent : "transparent"; border.width: 1.5 }
                        Row { id: chipRow; anchors.centerIn: parent; spacing: 6
                            PdIcon { path: "M4 5h16v10H4zM9 19h6M12 15v4"; size: 14; strokeWidth: 2; color: theme.accentFg; anchors.verticalCenter: parent.verticalCenter }
                            Text { text: peerChip.modelData.name; color: theme.accentFg; font.pixelSize: 12; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter } }
                        DropArea { id: drop; anchors.fill: parent; keys: ["text/uri-list"]
                            onDropped: (drop) => { if (drop.hasUrls) page.sendTo(peerChip.modelData, drop.urls); drop.accept() } }
                        HoverHandler { id: ph; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: page.pickFor(peerChip.modelData) }
                        Accessible.role: Accessible.Button; Accessible.name: "Send files to " + peerChip.modelData.name
                    }
                }
            }
        }
    }

    // ---- one line: the connection, with the one switch that matters ------------------------------------------------------------
    Item {
        id: connRow
        x: page.gutter; y: nearbyRow.y + nearbyRow.height + 6; width: page.innerW; height: 34
        visible: !page.pcActive
        readonly property bool offlineNow: page.mode === 2
        PdIcon { x: 2; anchors.verticalCenter: parent.verticalCenter; size: 16; strokeWidth: 1.9; color: theme.fg3
            path: "M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01" }
        Text { x: 28; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 28 - (connRow.offlineNow ? backChip.width : sw.width) - 12; elide: Text.ElideRight; color: theme.fg2; font.pixelSize: 13
            text: connRow.offlineNow ? "No router needed: your phone joins this hotspot" : "Reach my phone beyond this Wi-Fi" }
        PdSwitch {
            id: sw
            objectName: "modeSwitch"
            visible: !connRow.offlineNow
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            checked: page.mode === 1
            onToggled: pocketDrop.setMode(page.mode === 1 ? 0 : 1)
            Accessible.name: "Share beyond this Wi-Fi"
        }
        PdChip { id: backChip; visible: connRow.offlineNow; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; label: "Back to Wi-Fi"; iconPath: "M15 6l-6 6 6 6"; onClicked: pocketDrop.setMode(0) }
    }

    // ---- one line: transfer, result, receive code, bundle size or a hint --------------------------------------------------------
    Item {
        id: infoRow
        x: page.gutter; y: (page.pcActive ? cardBox.y + cardBox.height + 10 : connRow.y + connRow.height + 4); width: page.innerW; height: 30
        readonly property var i: page.d.info || ({})
        readonly property string kind: i.kind || "hint"
        // On the local network the QR carries the code, so it is only shown on request; an Anywhere link may be forwarded, so it is shown.
        readonly property bool codeShown: kind === "code" && !page.pcActive && (page.mode === 1 || page.showCode)
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
            objectName: "codeRow"
            visible: infoRow.codeShown; anchors.centerIn: parent; spacing: 8
            Text { text: "Receive code"; color: theme.fg3; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            Rectangle { width: codeT.implicitWidth + 16; height: 26; radius: 7; color: Qt.alpha(theme.accent, 0.14); anchors.verticalCenter: parent.verticalCenter
                Text { id: codeT; anchors.centerIn: parent; text: infoRow.i.code || ""; color: theme.accentFg; font.family: page.mono; font.pixelSize: 13; font.weight: Font.DemiBold; font.letterSpacing: 1.5 } }
        }
        Text { objectName: "codeHint"; visible: infoRow.kind === "code" && !infoRow.codeShown && !page.pcActive; anchors.centerIn: parent; color: theme.fg3; font.pixelSize: 12
            text: "Can’t scan? Show the receive code"
            TapHandler { onTapped: page.showCode = true }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            Accessible.role: Accessible.Button; Accessible.name: text }
        Row {
            visible: infoRow.kind === "done"; anchors.centerIn: parent; spacing: 7
            PdIcon { path: "M4.5 12.5l4.5 4.5 10.5-10.5"; size: 15; strokeWidth: 2.2; color: theme.win; anchors.verticalCenter: parent.verticalCenter }
            Text { text: infoRow.i.left || ""; color: theme.win; font.pixelSize: 12; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter }
        }
        Item {
            visible: infoRow.kind === "sum"; anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 17
            Text { width: parent.width - sr.implicitWidth - 10; elide: Text.ElideRight; text: infoRow.i.left || ""; color: theme.fg3; font.pixelSize: 12 }
            Text { id: sr; anchors.right: parent.right; text: infoRow.i.right || ""; color: theme.fg3; font.pixelSize: 12 }
        }
        Text { visible: infoRow.kind === "hint"; anchors.centerIn: parent; width: parent.width; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight; text: infoRow.i.left || ""; color: theme.fg3; font.pixelSize: 12 }
    }

    // ---- the one button: Add -------------------------------------------------------------------------------------------------
    Item {
        id: bar
        x: page.gutter; width: page.innerW; height: 38; y: page.height - 14 - height
        Row {
            spacing: 8; width: parent.width
            Item {
                id: addGroup
                width: parent.width - extraRow.width - clearBtn.width - (extraRow.children.length ? 8 : 0) - (clearBtn.visible ? 8 : 0); height: 38
                PdButton { id: addMain; objectName: "addButton"; width: parent.width - 36; height: 38; primary: true; label: "Add files"; iconPath: "M12 5v14M5 12h14"; onClicked: page.chooseFiles() }
                Rectangle { x: addMain.width + 1; width: 35; height: 38; radius: 12; color: addMore.hovered ? Qt.tint(theme.accent, Qt.alpha(theme.lift, 0.10)) : theme.accent
                    PdIcon { anchors.centerIn: parent; size: 14; strokeWidth: 2.4; color: theme.onAccent; path: "M6 9l6 6 6-6" }
                    HoverHandler { id: addMore; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: addMenu.opened ? addMenu.close() : addMenu.open() }
                    Accessible.role: Accessible.Button; Accessible.name: "More ways to add" }
            }
            Row { id: extraRow; spacing: 8; height: 38 }
            PdButton { id: clearBtn; visible: pocketDrop.rows.length > 0 && !page.pcActive; height: 38; width: 38; iconPath: "M6 6l12 12M18 6L6 18"; Accessible.name: "Clear everything"
                onClicked: pocketDrop.clearAll() }
        }
        Popup {
            id: addMenu
            y: -height - 6; width: 200; padding: 6; modal: false
            background: Rectangle { radius: 12; color: theme.s1; border.color: theme.line }
            contentItem: Column {
                Repeater {
                    model: [{ t: "Add folder…", k: "folder" }, { t: "Paste", k: "paste" }]
                    delegate: Item { required property var modelData; width: 188; height: 32
                        Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, h.hovered ? 0.08 : 0) }
                        Text { x: 10; anchors.verticalCenter: parent.verticalCenter; text: modelData.t; color: theme.fg; font.pixelSize: 13 }
                        HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: { addMenu.close(); if (modelData.k === "folder") page.chooseFolder(); else pocketDrop.command("paste") } } } }
            }
        }
    }

    // ---- shared and received items -----------------------------------------------------------------------------------------------
    ListView {
        id: list
        x: 10; y: infoRow.y + infoRow.height + 4; width: page.width - 20; height: Math.max(0, bar.y - 10 - y)
        clip: true; boundsBehavior: Flickable.StopAtBounds; model: pocketDrop.rows
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 5 }
        Text { visible: list.count === 0; x: 12; width: parent.width - 24; y: 8; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
            text: "Drop files here to share them.\nAnything your phone sends shows up here too."; color: theme.fg3; font.pixelSize: 12 }
        delegate: Item {
            id: row
            required property var modelData
            width: list.width; height: 46
            readonly property bool received: modelData.tone === "recv"
            readonly property bool isColl: modelData.kind === "coll"
            Rectangle { width: parent.width; height: 46; radius: 12; color: theme.s1; opacity: rowHover.hovered ? 1 : 0 }
            Item {
                x: 14; y: 8; width: row.isColl ? 54 : 30; height: 30
                Rectangle { visible: !row.isColl; anchors.fill: parent; radius: 9
                    color: row.received ? Qt.alpha(theme.win, 0.14) : modelData.kind === "note" ? Qt.alpha(theme.accent, 0.14) : theme.s3 }
                PdIcon { visible: !row.isColl; anchors.centerIn: parent; size: 16; strokeWidth: 1.8
                    color: row.received ? theme.win : modelData.kind === "note" ? theme.accentFg : theme.fg2
                    path: modelData.kind === "folder" ? "M3 7a2 2 0 0 1 2 -2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2H5a2 2 0 0 1 -2 -2z"
                        : (modelData.kind === "note" || modelData.kind === "rnote") ? "M5 6h14M5 11h14M5 16h9" : "M7 3h7l5 5v12a1 1 0 0 1 -1 1H7a1 1 0 0 1 -1 -1V4a1 1 0 0 1 1 -1zM14 3v5h5" }
                Repeater {
                    model: row.isColl ? 3 : 0
                    delegate: Rectangle { required property int index; x: index * 16; width: 30; height: 30; radius: 8; color: theme.s3; border.color: theme.s1; border.width: 2
                        Loader { anchors.fill: parent; anchors.margins: 2; sourceComponent: page.thumb
                            onLoaded: item.path = Qt.binding(function() { return row.modelData.thumbs && row.modelData.thumbs[index] ? row.modelData.thumbs[index] : "" }) } }
                }
            }
            Column {
                x: 14 + (row.isColl ? 54 : 30) + 11; y: 6.5; width: parent.width - x - 40
                Text { width: parent.width; height: 18; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; text: modelData.title; color: theme.fg; font.pixelSize: 13 }
                Text { width: parent.width; height: 15; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; text: modelData.sub; color: row.received ? theme.win : theme.fg3; font.pixelSize: 11 }
            }
            Item {
                visible: rowHover.hovered; anchors.right: parent.right; anchors.rightMargin: 6; y: 9; width: 28; height: 28
                Rectangle { anchors.fill: parent; radius: 8; color: theme.s3; opacity: xHover.hovered ? 1 : 0 }
                PdIcon { anchors.centerIn: parent; path: "M6 6l12 12M18 6L6 18"; size: 14; strokeWidth: 1.8; color: xHover.hovered ? theme.fg : theme.fg3 }
                HoverHandler { id: xHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: pocketDrop.removeRow(row.modelData.key) }
                Accessible.role: Accessible.Button; Accessible.name: "Remove " + modelData.title
            }
            HoverHandler { id: rowHover; cursorShape: row.received ? Qt.PointingHandCursor : Qt.ArrowCursor }
            TapHandler { enabled: row.received; onTapped: pocketDrop.command("row", String(row.modelData.index)) }
        }
    }

    // ---- overlays and dialogs ---------------------------------------------------------------------------------------------------
    DropArea {
        id: dropArea
        anchors.fill: parent
        keys: ["text/uri-list", "text/plain"]
        onDropped: (drop) => { if (drop.hasUrls) pocketDrop.addUrls(drop.urls); else if (drop.hasText) pocketDrop.addText(drop.text); drop.accept() }
    }
    PdMenu { id: menu; view: page; d: page.d; x: page.width - width - page.gutter; y: top.y + top.height + 4 }
    PdPairing { anchors.fill: parent; pair: page.pc.pair || ({}) }
    PdInvite { anchors.fill: parent; invite: page.nearby.invite || null }

    Rectangle {
        id: risk
        anchors.fill: parent; visible: page.handleRisk && pocketDrop.riskPending; color: Qt.alpha(theme.base, 0.82)
        MouseArea { anchors.fill: parent }
        Rectangle {
            anchors.centerIn: parent; width: Math.min(parent.width - 32, 340); height: rcol.height + 36; radius: 18; color: theme.s1; border.color: theme.line
            Column { id: rcol; x: 18; y: 18; width: parent.width - 36; spacing: 10
                Text { width: parent.width; text: "Before using Anywhere"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold }
                Text { width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12; lineHeight: 1.25
                    text: "Anyone with the active link can view and download the files you share until the link expires or you start a new one. Sending files back to this computer still needs the receive code." }
                CheckBox { id: dontAsk; text: "Don’t ask again"; palette.windowText: theme.fg2 }
                Row { spacing: 10; anchors.right: parent.right
                    PdButton { label: "Cancel"; width: 96; onClicked: pocketDrop.confirmRisk(false, false) }
                    PdButton { label: "Continue"; primary: true; width: 96; onClicked: pocketDrop.confirmRisk(true, dontAsk.checked) } }
            }
        }
    }

    Component { id: sendDialog
        FileDialog { title: "Choose files to send"; fileMode: FileDialog.OpenFiles
            onAccepted: { if (page.sendTarget) page.sendTo(page.sendTarget, selectedFiles); page.sendTarget = null }
            onRejected: page.sendTarget = null } }
    Component { id: filesDialog
        FileDialog { title: "Choose files to share"; fileMode: FileDialog.OpenFiles; onAccepted: pocketDrop.addUrls(selectedFiles) } }
    Component { id: folderDialog
        FolderDialog { title: "Choose a folder to share"; onAccepted: pocketDrop.addUrls([selectedFolder]) } }
    Component { id: inboxDialog
        FolderDialog { title: "Choose where received files are saved"; onAccepted: pocketDrop.changeInbox(selectedFolder) } }
}
