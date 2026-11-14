import QtQuick

// Best-of-Set Auto Assembly: a strategy, a size, and a shortlist of what is in view. Two sources (bestof_model.py):
//  * "ai": the AI report already saved for the folder. Top photos overall, per stack or per person, by a score the report really
//    holds; nothing is analysed again. The picks can be marked as Winners or added to a collection in the same step.
//  * "mine": the planner over your own winners (the earlier behaviour), for a folder with no saved report or when you prefer it.
Backdrop {
    id: root
    objectName: "bestOfDialog"
    shown: UiState.bestOfOpen
    property string strat: "balanced"
    property int n: 6
    readonly property var strategies: bestOf.strategies()
    readonly property int total: bestOf.total
    // -- choosing from the saved AI report
    readonly property var ai: bestOf.aiInfo
    property string source: "mine"
    property bool sourceChosen: false            // the person (or a test hook) picked a source: it is not switched under them
    property string aiStrat: "ai_overall"
    property string measure: ""
    property string action: "show"
    property string collectionId: ""
    readonly property var coll: { void collections.revision; return collections.pickerEntries() }
    readonly property var pv: source === "ai" && ai.ready ? bestOf.preview(aiStrat, measure, n) : ({ count: 0, problem: "", note: "" })
    readonly property bool canBuild: source === "ai" ? (ai.ready && pv.problem === "" && pv.count > 0 && (action !== "collection" || collectionId !== "")) : total > 0
    function followReport() {
        if (!sourceChosen) source = ai.ready ? "ai" : "mine"
        var ok = false
        for (var i = 0; i < ai.measures.length; ++i) if (ai.measures[i].value === measure && (ai.measures[i].overall || aiStrat === "ai_stack")) ok = true
        if (!ok) measure = bestOf.defaultMeasure(aiStrat)
    }
    onAiChanged: followReport()
    onAiStratChanged: followReport()
    onShownChanged: {
        if (!shown) return
        sourceChosen = UiState.boStrat !== ""
        strat = UiState.boStrat !== "" ? UiState.boStrat : strategies[0].value
        n = UiState.boN > 0 ? UiState.boN : bestOf.defaultSize()
        source = "mine"; action = "show"
        collectionId = coll.length ? coll[0].id : ""
        bestOf.prepare()                         // reads the saved report off the UI thread; `ai` follows when it is in
        followReport()
        forceActiveFocus()
    }
    function close() { UiState.bestOfOpen = false }
    function build() {
        if (!canBuild) return
        if (source === "ai") { if (!bestOf.buildFromReport(aiStrat, measure, n, action, collectionId)) return }
        else bestOf.build(strat, n)
        close()
    }
    function showHelp() {
        UiState.boStrat = strat
        UiState.boN = n
        close()
        reference.openTopic("best")
    }
    onDismissed: close()
    Keys.onReturnPressed: build()

    component Choice: Item {
        id: opt
        property string label
        property string desc
        property bool on: false
        signal chosen()
        width: col.width; height: 20 + body.implicitHeight
        Rectangle { anchors.fill: parent; radius: 12; color: opt.on ? Qt.tint(theme.base, Qt.alpha(theme.accent, 0.10)) : theme.base
                    border.width: opt.on ? 1.5 : 1; border.color: opt.on ? Qt.alpha(theme.accent, 0.70) : (oh.hovered ? theme.line : theme.lineSoft) }
        Rectangle { x: 12; y: 12; width: 16; height: 16; radius: 8; color: "transparent"; border.width: opt.on ? 5 : 1.6; border.color: opt.on ? theme.accent : theme.fg3 }
        Column { id: body; x: 40; y: 10; width: parent.width - 52; spacing: 2
            Text { text: opt.label; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; height: 18 }
            Text { width: parent.width; text: opt.desc; color: theme.fg3; font.pixelSize: 12; wrapMode: Text.WordWrap; lineHeight: 16.8; lineHeightMode: Text.FixedHeight } }
        HoverHandler { id: oh; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: opt.chosen() }
        Accessible.role: Accessible.RadioButton; Accessible.name: opt.label; Accessible.checked: opt.on
    }

    DialogCard {
        shown: root.shown
        width: 500; height: col.implicitHeight + 46
        x: (parent.width - width) / 2; y: (parent.height - height) / 2
        Column {
            id: col
            x: 23; y: 23; width: parent.width - 46
            Item {
                width: parent.width; height: 28
                Text { width: parent.width - 40; anchors.verticalCenter: parent.verticalCenter; text: "Choose your best photos"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15 }
                SButton { objectName: "bestOfHelpButton"; anchors.right: parent.right; text: "?"; width: 28; h: 28; radius: 14
                    Accessible.name: "How to choose your best photos"
                    onClicked: root.showHelp() }
            }
            Item { width: 1; height: 6 }
            Text { width: parent.width; wrapMode: Text.WordWrap; text: "Choose how to pick from the photos shown. Review the picks in the grid, then use Back to folder to return."
                   color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
            Item { width: 1; height: 14 }
            Seg { objectName: "bestOfSource"; width: parent.width; height: 36; fill: true; showExtras: false; labels: ["From saved AI results", "From my winners"]
                  current: root.source === "ai" ? 0 : 1
                  onActivated: (i) => { root.sourceChosen = true; root.source = i === 0 ? "ai" : "mine" } }
            Item { width: 1; height: 12 }
            // ---- from the saved AI report ----
            Column {
                visible: root.source === "ai"; width: parent.width; spacing: 6
                // no report, or it is still being read: said plainly, with nothing to choose
                Rectangle { objectName: "bestOfNoReport"; visible: !root.ai.ready; width: parent.width; height: noText.implicitHeight + 24; radius: 12; color: theme.base; border.color: theme.lineSoft
                    Text { id: noText; x: 12; y: 12; width: parent.width - 24; wrapMode: Text.WordWrap; text: root.ai.why; color: theme.fg2; font.pixelSize: 12; lineHeight: 17; lineHeightMode: Text.FixedHeight } }
                Repeater {
                    model: root.ai.ready ? root.ai.strategies : []
                    delegate: Choice { required property var modelData; objectName: "bestOfAi-" + modelData.value
                        label: modelData.label; desc: modelData.desc; on: root.aiStrat === modelData.value; onChosen: root.aiStrat = modelData.value }
                }
                Item { visible: root.ai.ready; width: parent.width; height: 44
                    Text { anchors.verticalCenter: parent.verticalCenter; text: "Rank by"; color: theme.fg2; font.pixelSize: 13 }
                    SSelect { objectName: "bestOfMeasure"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 300; name: "Rank by"
                        options: root.ai.measures; current: root.measure; onPicked: (value) => root.measure = value }
                    Text { visible: root.ai.measures.length === 0; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "no recorded score"; color: theme.fg3; font.pixelSize: 12 } }
                Item { visible: root.ai.ready; width: parent.width; height: 40
                    Text { anchors.verticalCenter: parent.verticalCenter; text: "Then"; color: theme.fg2; font.pixelSize: 13 }
                    Row { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                        SSelect { objectName: "bestOfAction"; width: root.action === "collection" ? 190 : 300; name: "What to do with the picks"
                            options: root.ai.actions; current: root.action; onPicked: (value) => root.action = value }
                        SSelect { objectName: "bestOfCollection"; visible: root.action === "collection" && root.coll.length > 0; width: 102; name: "Collection"
                            options: root.coll.map(c => ({ label: c.name, value: c.id })); current: root.collectionId; onPicked: (value) => root.collectionId = value } } }
                Text { visible: root.ai.ready && root.action === "collection" && root.coll.length === 0; width: parent.width; wrapMode: Text.WordWrap; color: theme.rev; font.pixelSize: 12
                       text: "There is no collection yet. Create one under Collections, then choose it here." }
                // what this choice would give, or why it gives nothing: read from the report before anything is changed
                Text { objectName: "bestOfPreview"; visible: root.ai.ready; width: parent.width; wrapMode: Text.WordWrap; font.pixelSize: 12; lineHeight: 17; lineHeightMode: Text.FixedHeight
                       color: root.pv.problem !== "" ? theme.rev : theme.fg3
                       text: root.pv.problem !== "" ? root.pv.problem : (root.pv.count + (root.pv.count === 1 ? " photo" : " photos") + " would be chosen from the saved results; nothing is analyzed again." + (root.pv.note ? " " + root.pv.note : "")) }
            }
            // ---- from my winners (the planner) ----
            Column {
                visible: root.source !== "ai"; width: parent.width; spacing: 6
                Text { objectName: "bestOfNoReportHint"; visible: root.ai.state === "none"; width: parent.width; wrapMode: Text.WordWrap; bottomPadding: 4; color: theme.fg3; font.pixelSize: 12
                       text: "This folder has no saved AI results, so the picks come from your own winners." }
                Repeater {
                    model: root.strategies
                    delegate: Choice { required property var modelData
                        label: modelData.label; desc: modelData.desc; on: root.strat === modelData.value; onChosen: root.strat = modelData.value }
                }
            }
            Item { width: 1; height: 16 }
            Item {
                visible: root.source !== "ai" || root.ai.ready
                width: parent.width; height: 34
                Text { anchors.verticalCenter: parent.verticalCenter; color: theme.fg2; font.pixelSize: 13
                       text: root.source !== "ai" || root.aiStrat === "ai_overall" ? "Photos to pick" : root.aiStrat === "ai_stack" ? "Photos per stack" : "Photos per person" }
                Text { id: ofText; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "of " + root.total + (root.total === 1 ? " photo" : " photos"); color: theme.fg3; font.pixelSize: 12 }
                Rectangle {
                    anchors.right: ofText.left; anchors.rightMargin: 12; height: 34; width: 32 + 36 + 32; radius: 10; color: theme.base; border.color: theme.lineSoft
                    Item { width: 32; height: parent.height
                        Text { anchors.centerIn: parent; text: "−"; color: mh.hovered ? theme.fg : theme.fg2; font.pixelSize: 16 }
                        HoverHandler { id: mh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.n = Math.max(1, root.n - 1) }
                        Accessible.role: Accessible.Button; Accessible.name: "Fewer" }
                    Text { x: 32; width: 36; height: parent.height; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; text: root.n; color: theme.fg; font.pixelSize: 13 }
                    Item { x: 68; width: 32; height: parent.height
                        Text { anchors.centerIn: parent; text: "+"; color: ph.hovered ? theme.fg : theme.fg2; font.pixelSize: 16 }
                        HoverHandler { id: ph; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.n = Math.min(Math.max(1, root.total), root.n + 1) }
                        Accessible.role: Accessible.Button; Accessible.name: "More" }
                }
            }
            Item { width: 1; height: 20 }
            Row { anchors.right: parent.right; spacing: 8
                SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: root.close() }
                SButton { objectName: "bestOfBuild"; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: root.canBuild; onClicked: root.build()
                          text: root.source === "ai" && root.action === "win" ? "Mark as Winners" : root.source === "ai" && root.action === "collection" ? "Add to collection" : "Show picks" }
            }
        }
    }
}
