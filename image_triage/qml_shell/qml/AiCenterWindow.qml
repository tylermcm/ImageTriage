import QtQuick
import QtQuick.Dialogs

// AI Center: what the cull needs on this computer (runtime, graphics card, models), the defaults, and recent runs. Model: cull_model.py, which in the
// app gets every state and every button's work from ai_center.py (what is really installed, and the Widgets app's own installers).
Backdrop {
    id: root
    objectName: "aiCenterWindow"
    readonly property var c: cull.view.center
    shown: cull.view.window === "center"
    onDismissed: cull.close()
    z: 107
    function dotColor(k) { return k === "ok" ? theme.win : k === "bad" ? theme.rej : theme.rev }
    // a model row's button: cancel what is running, ask before deleting a ranker, otherwise install or repair
    // which model cards have their list of models open (closed when the window opens)
    property var openGroups: ({})
    onShownChanged: if (shown) openGroups = ({})
    function toggleGroup(key) { var o = Object.assign({}, openGroups); o[key] = !o[key]; openGroups = o }
    // a card's Cancel: stops its model that is downloading and takes its waiting ones out of the queue
    function cancelGroup(g) { for (var i = 0; i < g.rows.length; ++i) if (g.rows[i].busy || g.rows[i].queued) cull.cancelModel(g.rows[i].key) }
    function modelPressed(m) {
        if (m.busy || m.queued) { cull.cancelModel(m.key); return }
        if (m.pick) { modelFile.key = m.key; modelFile.open(); return }
        if (m.confirm) { var t = cull.removeText(m.key); removeCard.key = m.key; removeCard.title = t.title; removeCard.body = t.body; removeCard.open(); return }
        cull.modelAction(m.key)
    }
    // the runtime's button: cancel, or say what will be downloaded first (it is gigabytes) and ask
    function runtimePressed() {
        if (c.rtInstalling) { cull.cancelSetup(); return }
        var p = cull.setupPlan(); setupCard.title = p.title; setupCard.body = p.body; setupCard.confirmText = p.confirm; setupCard.open()
    }
    // an editor model's .onnx file picked by hand (a row with "pick"; none offers it now that they download): recognised by its checksum and copied in
    FileDialog {
        id: modelFile; property string key
        title: "Choose the model file"; nameFilters: ["ONNX models (*.onnx)"]
        onAccepted: cull.installModelFile(key, decodeURIComponent(String(selectedFile).replace(/^file:\/\/\/?/, Qt.platform.os === "windows" ? "" : "/")))
    }
    ConfirmCard { id: removeCard; objectName: "aiRemoveCard"; property string key; confirmText: "Remove"; cancelText: "Cancel"; onAccepted: cull.modelAction(removeCard.key) }
    ConfirmCard { id: setupCard; objectName: "aiSetupCard"; cancelText: "Cancel"; onAccepted: cull.setupRuntime() }
    // removing the runtime deletes gigabytes that a later setup downloads again, so it says what goes and what stays first
    ConfirmCard { id: runtimeRemoveCard; objectName: "aiRuntimeRemoveCard"; warn: true; cancelText: "Cancel"; onAccepted: cull.removeRuntime() }
    function removeRuntimePressed() { var t = cull.removeRuntimeText(); runtimeRemoveCard.title = t.title; runtimeRemoveCard.body = t.body; runtimeRemoveCard.confirmText = t.confirm; runtimeRemoveCard.open() }
    // neither of these can be taken back, so both ask first
    ConfirmCard { id: clearCard; objectName: "aiClearCard"; confirmText: "Clear"; cancelText: "Cancel"; title: "Clear the results shown here?"
                  body: "This forgets what the Cull window found for the open folder (a running analysis is stopped) and empties the list of recent runs. Your photos and your decisions are not touched."
                  onAccepted: cull.clearRuns() }
    ConfirmCard { id: deleteCard; objectName: "aiDeleteDataCard"; warn: true; cancelText: "Cancel"; onAccepted: cull.deleteFolderData() }
    ConfirmCard { id: forgetCard; objectName: "aiForgetPeopleCard"; warn: true; cancelText: "Cancel"; onAccepted: cull.forgetPeople() }
    function forgetPressed() { var t = cull.forgetPeopleText(); forgetCard.title = t.title; forgetCard.body = t.body; forgetCard.confirmText = t.confirm; forgetCard.open() }
    function deletePressed() { var t = cull.deleteDataText(); deleteCard.title = t.title; deleteCard.body = t.body; deleteCard.confirmText = t.confirm; deleteCard.open() }
    component Card: Rectangle { radius: 14; color: theme.s1; border.color: theme.lineSoft }
    component H3: Text { height: 13; verticalAlignment: Text.AlignVCenter; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
    component Status: Row { property string tone; property string label; spacing: 6; anchors.verticalCenter: parent ? parent.verticalCenter : undefined
        Rectangle { width: 8; height: 8; radius: 4; color: root.dotColor(tone); anchors.verticalCenter: parent.verticalCenter }
        Text { text: label; color: theme.fg2; font.pointSize: UiState.pt(11.5); font.weight: Font.Medium } }
    // one row: an icon, a title with a status, a line of text and what the person can do
    component Row2: Card {
        id: r2
        property string iconPath; property string title; property string tone; property string state; property string desc
        property bool busy: false; property int pct: 0; property string step
        default property alias actions: rightSlot.data
        width: parent ? parent.width : 600; height: Math.max(texts.implicitHeight, 36) + 28
        Rectangle { x: 14; y: 14; width: 36; height: 36; radius: 11; color: theme.s2; border.color: theme.lineSoft; visible: iconPath !== ""
            Icon { anchors.centerIn: parent; path: r2.iconPath; size: 19; color: theme.fg2 } }
        Column { id: texts; x: iconPath !== "" ? 64 : 14; y: 14; width: parent.width - x - rightSlot.width - 28; spacing: 4
            Row { spacing: 8; Text { text: r2.title; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium } Status { visible: r2.state !== ""; tone: r2.tone; label: r2.state } }
            Text { width: parent.width; text: r2.desc; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight }
            Item { visible: r2.busy; width: parent.width; height: 28
                Rectangle { y: 10; width: parent.width; height: 5; radius: 2.5; color: theme.s3
                    Rectangle { width: parent.width * r2.pct / 100; height: 5; radius: 2.5; gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: theme.accent } GradientStop { position: 1; color: "#d58ae8" } } } }
                Text { y: 14; text: r2.step; color: theme.fg3; font.pointSize: UiState.pt(12) } } }
        Row { id: rightSlot; anchors.right: parent.right; anchors.rightMargin: 14; y: 14; height: 36; spacing: 8 }
    }

    DialogCard {
        shown: root.shown
        width: Math.min(760, root.width - 64); height: Math.min(680, root.height - 72)
        x: (parent.width - width) / 2; y: (parent.height - height) / 2
        RoundClip {
            anchors.fill: parent; radius: 16
            Column { id: hd; x: 26; y: 20; width: parent.width - 52
                Text { text: "AI Center"; color: theme.fg; font.pixelSize: 18; font.weight: Font.DemiBold; font.letterSpacing: -0.27 }
                Text { width: Math.min(560, parent.width); topPadding: 5; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight
                       text: "What the cull needs to run on this computer, and the runs you have made. Nothing here uploads photos: every model runs on this PC." } }
            Flickable {
                id: body; objectName: "aiCenterBody"
                y: hd.y + hd.height + 6; width: parent.width; height: parent.height - y - 60
                contentWidth: width; contentHeight: col.height + 18; clip: true; boundsBehavior: Flickable.StopAtBounds
                Column { id: col; x: 26; width: body.width - 52; spacing: 0
                    Item { width: 1; height: 12 }
                    H3 { text: "ENGINE"; width: parent.width }
                    Item { width: 1; height: 9 }
                    Row2 { iconPath: "M7 7h10v10H7zM4 9h3M4 15h3M17 9h3M17 15h3M9 4v3M15 4v3M9 17v3M15 17v3"; title: "AI runtime"; tone: root.c.rtDot; state: root.c.rtState; desc: root.c.rtDesc; busy: root.c.rtInstalling || !!root.c.rtRemoving; pct: root.c.rtPct; step: root.c.rtStep
                        SButton { objectName: "aiRuntimeButton"; visible: root.c.rtBtnShow !== false; text: root.c.rtBtn; variant: root.c.rtPrimary ? "primary" : "normal"; h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter; onClicked: root.runtimePressed() }
                        // Files on disk only say "installed". This starts the runtime for real and reports the device it used.
                        SButton { objectName: "aiRuntimeTest"; visible: !!root.c.rtTestShow; text: root.c.rtTestBtn || "Test runtime"; h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter
                                  onClicked: root.c.rtTesting ? cull.cancelSetup() : cull.testRuntime() }
                        SButton { objectName: "aiRuntimeRemove"; visible: !!root.c.rtRemoveShow; text: "Remove…"; h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter; onClicked: root.removeRuntimePressed() } }
                    Item { width: 1; height: 8 }
                    Row2 { iconPath: "M4 5h16v10H4zM9 19h6M12 15v4"; title: "Graphics card"; tone: root.c.gpuDot; state: root.c.gpuState; desc: root.c.gpuDesc }
                    Item { visible: root.c.vcShow; width: 1; height: 8 }
                    Row2 { visible: root.c.vcShow; iconPath: "M12 8v5M12 16.5v.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"; title: "Visual C++ update recommended"; tone: "warn"; state: ""
                        desc: "Found " + root.c.vcFound + ". Version " + (root.c.vcNeeded || "14.40") + " or newer is recommended. This check does not prevent AI tasks from running."
                        SButton { text: "Get the update"; variant: "primary"; h: 28; hpad: 11; radius: 9; onClicked: Qt.openUrlExternally("https://aka.ms/vs/17/release/vc_redist.x64.exe") }
                        SButton { text: "Check again"; h: 28; hpad: 11; radius: 9; onClicked: cull.recheckRuntime() } }
                    Item { width: 1; height: 16 }
                    H3 { text: "MODELS"; width: parent.width }
                    Item { width: 1; height: 9 }
                    // One card per group of models: its button installs everything the group lacks (one after another), and the
                    // chevron beside it opens the list of the group's models, each with its own button, to pick from.
                    Repeater { model: root.c.groups || []
                        delegate: Column {
                            id: grp
                            required property var modelData
                            readonly property bool open: !!root.openGroups[modelData.key]
                            width: col.width; spacing: 6; bottomPadding: 8
                            // an opened list is scrolled into view, as far as it fits, its card staying at the top
                            onOpenChanged: if (open) { var view = body, card = grp; Qt.callLater(function() {
                                if (!view || !card) return             // the window closed first
                                var bottom = card.y + card.height
                                if (bottom > view.contentY + view.height)
                                    view.contentY = Math.max(0, Math.min(card.y, bottom - view.height, view.contentHeight - view.height))
                            }) }
                            Row2 { iconPath: grp.modelData.key === "cull" ? "M5 19V10M12 19V5M19 19v-7" : grp.modelData.key === "search" ? "M11 18a7 7 0 100-14 7 7 0 000 14zM20 20l-4-4" : "M12 3.5l1.8 4.7 4.7 1.8-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.8z"
                                title: grp.modelData.name; tone: grp.modelData.tone; state: grp.modelData.state; desc: grp.modelData.desc; busy: grp.modelData.busy; pct: grp.modelData.pct; step: grp.modelData.step
                                SButton { objectName: "aiGroupButton-" + grp.modelData.key; visible: grp.modelData.btn !== ""; text: grp.modelData.btn; variant: grp.modelData.primary ? "primary" : "normal"
                                          h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter
                                          onClicked: grp.modelData.busy ? root.cancelGroup(grp.modelData) : cull.installGroup(grp.modelData.key) }
                                Rectangle {
                                    objectName: "aiGroupChooser-" + grp.modelData.key
                                    width: 28; height: 28; radius: 9; anchors.verticalCenter: parent.verticalCenter
                                    color: chooseHover.hovered ? Qt.tint(theme.s3, Qt.alpha(theme.lift, 0.06)) : theme.s3; border.color: theme.lineSoft
                                    Icon { anchors.centerIn: parent; path: "M6 9l6 6 6-6"; size: 15; color: theme.fg2; rotation: grp.open ? 180 : 0
                                           Behavior on rotation { NumberAnimation { duration: 140 } } }
                                    HoverHandler { id: chooseHover; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: root.toggleGroup(grp.modelData.key) }
                                    Accessible.role: Accessible.Button; Accessible.name: (grp.open ? "Hide the models of " : "Choose models of ") + grp.modelData.name } }
                            Repeater { model: grp.open ? grp.modelData.rows : []
                                delegate: Item { required property var modelData; x: 22; width: col.width - 22; height: mr.height
                                    Rectangle { x: -12; y: -6; width: 2; height: parent.height + 6; radius: 1; color: theme.lineSoft }
                                    Row2 { id: mr; iconPath: ""; title: modelData.name; tone: modelData.tone; state: modelData.state; desc: modelData.desc; busy: modelData.busy; pct: modelData.pct; step: modelData.step
                                        SButton { objectName: "aiModelButton-" + modelData.key; text: modelData.btn; variant: modelData.primary ? "primary" : "normal"; enabled: !modelData.locked
                                                  h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter; onClicked: root.modelPressed(modelData) } } } } } }
                    Item { width: 1; height: 8 }
                    H3 { text: "DEFAULTS"; width: parent.width }
                    Item { width: 1; height: 9 }
                    Card { width: parent.width; height: 62
                        Column { x: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                            Text { text: "Rank on the CPU when there is no GPU"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                            Text { text: "Off by default. Ranking on a CPU is slow, and you are asked each time."; color: theme.fg3; font.pointSize: UiState.pt(12.5) } }
                        SSwitch { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; checked: root.c.cpu; onToggled: (v) => cull.setCpuDefault(v) } }
                    Item { width: 1; height: 8 }
                    Card { width: parent.width; height: 62
                        Column { x: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                            Text { text: "How strict by default"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                            Text { text: "The Cull window starts here. You can change it for any run."; color: theme.fg3; font.pointSize: UiState.pt(12.5) } }
                        Seg { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; width: 240; height: 38; fill: true; showExtras: false; labels: ["Careful", "Balanced", "Strict"]
                              current: { var s = cull.view.sens, d = root.c.defSens || ""; for (var i = 0; i < s.length; ++i) if (s[i].key === d) return i; return 1 }
                              onActivated: (i) => cull.setDefaultSens(cull.view.sens[i].key) } }
                    Item { visible: folderData.visible; width: 1; height: 16 }
                    H3 { visible: folderData.visible; text: "THIS FOLDER"; width: parent.width }
                    Item { visible: folderData.visible; width: 1; height: 9 }
                    Row2 { id: folderData; objectName: "aiFolderData"; visible: (root.c.dataFolder || "") !== ""; iconPath: "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"
                        title: "AI data for " + (root.c.dataFolder || ""); tone: ""; state: ""
                        desc: "The analysis keeps its results, the search index, and the faces and names it found in a hidden folder beside these photos. Deleting it removes all of that for this folder; your photos and decisions stay."
                        SButton { objectName: "aiDeleteDataButton"; text: "Delete AI data…"; h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter; onClicked: root.deletePressed() } }
                    // People named in one folder are recognised in the next. Their names and face data stay on this PC; this row
                    // switches that off and deletes what is remembered (people_identities.py).
                    Item { visible: peopleMemory.visible; width: 1; height: 16 }
                    H3 { visible: peopleMemory.visible; text: "PEOPLE ACROSS FOLDERS"; width: parent.width }
                    Item { visible: peopleMemory.visible; width: 1; height: 9 }
                    Row2 { id: peopleMemory; objectName: "aiPeopleMemory"; visible: !!root.c.peopleMemory && !!root.c.peopleMemory.show
                        iconPath: "M16 11a3 3 0 100-6 3 3 0 000 6zM8 11a3 3 0 100-6 3 3 0 000 6zM2 19c0-2.8 2.7-5 6-5s6 2.2 6 5M14.5 14.3c.5-.2 1-.3 1.5-.3 3.3 0 6 2.2 6 5"
                        title: "Remember named people"; tone: root.c.peopleMemory && root.c.peopleMemory.on ? "ok" : "warn"
                        state: root.c.peopleMemory && root.c.peopleMemory.on ? "On" : "Off"; desc: root.c.peopleMemory ? root.c.peopleMemory.desc : ""
                        SButton { objectName: "aiForgetPeopleButton"; visible: !!root.c.peopleMemory && root.c.peopleMemory.canForget; text: "Forget everyone…"; h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter; onClicked: root.forgetPressed() }
                        SSwitch { objectName: "aiPeopleMemorySwitch"; anchors.verticalCenter: parent.verticalCenter; checked: !!root.c.peopleMemory && root.c.peopleMemory.on; name: "Remember named people across folders"
                                  onToggled: (v) => cull.setPeopleMemory(v) } }
                    Item { width: 1; height: 16 }
                    H3 { text: "RECENT RUNS"; width: parent.width }
                    Item { width: 1; height: 9 }
                    Card { width: parent.width; height: 32 + Math.max(1, root.c.runs.length) * 42 - (root.c.runs.length ? 0 : 0); clip: true
                        Rectangle { width: parent.width; height: 32; color: theme.s2; radius: 14
                            Rectangle { y: 18; width: parent.width; height: 14; color: theme.s2 }
                            Row { x: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 12
                                Repeater { model: [["FOLDER", 190], ["WHEN", 100], ["FRAMES", 60], ["FLAGGED", 60], ["WHAT YOU DID", 170]]
                                    delegate: Text { required property var modelData; width: modelData[1]; text: modelData[0]; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.6 } } } }
                        Column { y: 32; width: parent.width
                            Repeater { model: root.c.runs
                                delegate: Item { id: rr; required property var modelData; width: col.width; height: 42
                                    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                                    Row { x: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 12
                                        Text { width: 190; elide: Text.ElideRight; text: rr.modelData.folder; color: theme.fg; font.pointSize: UiState.pt(12.5); font.weight: Font.Medium }
                                        Text { width: 100; text: rr.modelData.when; color: theme.fg2; font.pointSize: UiState.pt(12.5) }
                                        Text { width: 60; text: rr.modelData.frames; color: theme.fg2; font.pointSize: UiState.pt(12.5) }
                                        Text { width: 60; text: rr.modelData.flagged; color: theme.fg2; font.pointSize: UiState.pt(12.5) }
                                        Text { width: 150; elide: Text.ElideRight; text: rr.modelData.did; color: theme.fg2; font.pointSize: UiState.pt(12.5) } }
                                    Item { visible: rr.modelData.current; anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; width: ol.implicitWidth + 8; height: 20
                                        Text { id: ol; anchors.centerIn: parent; text: "Open"; color: theme.accentFg; font.pointSize: UiState.pt(12); font.weight: Font.Medium }
                                        TapHandler { onTapped: cull.openReview() }
                                        HoverHandler { cursorShape: Qt.PointingHandCursor } } } }
                            Text { visible: root.c.noRuns; width: parent.width; topPadding: 12; horizontalAlignment: Text.AlignHCenter; text: "No runs yet. Analyze a folder and it shows up here."; color: theme.fg3; font.pointSize: UiState.pt(12.5) } } }
                }
            }
            Rectangle {
                y: parent.height - 60; width: parent.width; height: 60; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                Text { x: 26; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 290; elide: Text.ElideRight; color: theme.fg3; font.pointSize: UiState.pt(12.5); text: "Results are kept with each folder; clearing them never touches your photos." }
                Row { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { objectName: "aiClearButton"; text: "Clear all results"; h: 34; hpad: 16; radius: 12; onClicked: clearCard.open() }
                    SButton { text: "Done"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: cull.close() } }
            }
        }
    }
}
