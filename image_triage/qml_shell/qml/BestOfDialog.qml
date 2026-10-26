import QtQuick

// Best-of-Set Auto Assembly: a strategy, a size, and the planner builds a shortlist of what is in view.
Backdrop {
    id: root
    objectName: "bestOfDialog"
    shown: UiState.bestOfOpen
    property string strat: "balanced"
    property int n: 6
    readonly property var strategies: bestOf.strategies()
    readonly property int total: bestOf.total
    onShownChanged: {
        if (!shown) return
        strat = UiState.boStrat !== "" ? UiState.boStrat : strategies[0].value
        n = UiState.boN > 0 ? UiState.boN : bestOf.defaultSize()
        forceActiveFocus()
    }
    function close() { UiState.bestOfOpen = false }
    function build() { bestOf.build(strat, n); close() }
    onDismissed: close()
    Keys.onReturnPressed: build()

    DialogCard {
        shown: root.shown
        width: 500; height: col.implicitHeight + 46
        x: (parent.width - width) / 2; y: 120
        Column {
            id: col
            x: 23; y: 23; width: parent.width - 46
            Text { width: parent.width; height: 20.25; verticalAlignment: Text.AlignVCenter; text: "Best-of-Set Auto Assembly"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15 }
            Item { width: 1; height: 6 }
            Text { width: parent.width; wrapMode: Text.WordWrap; text: "Build an editable shortlist from the current set. The shortlist takes the place of the grid until you go back to the folder, so you can review and override it immediately."
                   color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
            Item { width: 1; height: 16 }
            Column {
                width: parent.width; spacing: 6
                Repeater {
                    model: root.strategies
                    delegate: Item {
                        id: opt
                        required property var modelData
                        readonly property bool on: root.strat === modelData.value
                        width: col.width; height: 20 + body.implicitHeight
                        Rectangle { anchors.fill: parent; radius: 12; color: opt.on ? Qt.tint(theme.base, Qt.alpha(theme.accent, 0.10)) : theme.base
                                    border.width: opt.on ? 1.5 : 1; border.color: opt.on ? Qt.alpha(theme.accent, 0.70) : (oh.hovered ? theme.line : theme.lineSoft) }
                        Rectangle { x: 12; y: 12; width: 16; height: 16; radius: 8; color: "transparent"; border.width: opt.on ? 5 : 1.6; border.color: opt.on ? theme.accent : theme.fg3 }
                        Column { id: body; x: 40; y: 10; width: parent.width - 52; spacing: 2
                            Text { text: opt.modelData.label; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; height: 18 }
                            Text { width: parent.width; text: opt.modelData.desc; color: theme.fg3; font.pixelSize: 12; wrapMode: Text.WordWrap; lineHeight: 16.8; lineHeightMode: Text.FixedHeight } }
                        HoverHandler { id: oh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.strat = opt.modelData.value }
                        Accessible.role: Accessible.RadioButton; Accessible.name: opt.modelData.label; Accessible.checked: opt.on
                    }
                }
            }
            Item { width: 1; height: 16 }
            Item {
                width: parent.width; height: 34
                Text { anchors.verticalCenter: parent.verticalCenter; text: "Proposal size"; color: theme.fg2; font.pixelSize: 13 }
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
                SButton { text: "Build shortlist"; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: root.total > 0; onClicked: root.build() }
            }
        }
    }
}
