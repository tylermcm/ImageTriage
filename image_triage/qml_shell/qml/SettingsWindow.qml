import QtQuick
import QtQuick.Controls
import QtQuick.Effects

// Settings, as a window over the dimmed and blurred application (not a page that replaces the grid). A QML overlay rather than a second
// operating-system window so the blur is of the live window behind it. Layout, sizes and colours follow the HTML mockup.
FocusScope {
    id: root
    property Item sourceItem                       // the application content to blur behind the window
    readonly property bool shown: UiState.settingsOpen
    anchors.fill: parent
    visible: opacity > 0.01
    opacity: shown ? 1 : 0
    z: 100
    Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

    // centred in the area between the top bar (42) and the status bar (26), as the mockup's layout box is
    readonly property real winW: Math.min(1060, width - 96)
    readonly property real winH: Math.min(700, height - 68 - 64)
    readonly property string section: UiState.settingsSection
    readonly property string query: UiState.settingsQuery
    readonly property var navSections: settings.sections()
    readonly property int navIndex: { for (var i = 0; i < navSections.length; ++i) if (navSections[i].id === section) return i; return 0 }
    readonly property var groups: { void settings.revision === undefined; return settings.page(section, query) }
    readonly property var info: settings.pageInfo(section)
    readonly property var counts: settings.counts(query)
    property string recording: ""

    function open() { UiState.settingsOpen = true; root.forceActiveFocus() }
    function openGuide() { reference.setQuery(""); reference.setTopic("settings"); reference.open("help") }       // the Help window's Settings guide, over this window
    function cancel() { settings.discard(); UiState.settingsOpen = false; UiState.settingsQuery = ""; root.recording = "" }
    onShownChanged: if (shown) { settings.readCatalogSummary(); forceActiveFocus() }
    Keys.onEscapePressed: root.cancel()

    // the dimmed, blurred application
    ShaderEffectSource { id: snap; anchors.fill: parent; sourceItem: root.sourceItem; live: true; visible: false }
    MultiEffect { anchors.fill: parent; source: snap; blurEnabled: true; blur: 1.0; blurMax: 20 }
    Rectangle { anchors.fill: parent; color: theme.scrim }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onWheel: (w) => { w.accepted = true } }       // modal: nothing behind is reachable

    Item {
        id: win
        x: (root.width - width) / 2; y: 42 + (root.height - 68 - height) / 2
        width: root.winW; height: root.winH
        opacity: UiState.guideCovers ? 0 : 1          // the guide, once fully in front and the same size, takes this window's place: it fades in over it
        scale: root.shown ? 1 : 0.96
        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }

        Shadow { anchors.fill: parent; radius: 14; offsetA: 48; insetA: 24; blurA: 64; multA: 3; alphaA: 0.85; offsetB: 12; insetB: 12; blurB: 24; alphaB: 0.5 }
        Rectangle { anchors.fill: parent; radius: 14; color: theme.base }
        Rectangle { anchors.fill: parent; radius: 14; color: "transparent"; border.color: theme.line; border.width: 1; z: 10 }

        RoundClip {
            anchors.fill: parent; radius: 14

            // ---- title bar -------------------------------------------------------------------------------------------------
            Rectangle {
                id: titleBar; x: 0; y: 0; width: parent.width; height: 40; color: theme.s1
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                Icon { x: 14; anchors.verticalCenter: parent.verticalCenter; size: 16; color: theme.fg2
                       path: "M12 9a3 3 0 100 6 3 3 0 000-6zM12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" }
                Text { x: 40; anchors.verticalCenter: parent.verticalCenter; text: "Image Triage settings"; color: theme.fg2; font.pixelSize: 13; font.weight: Font.Medium }
                Row {
                    anchors.right: parent.right; height: parent.height
                    Item { width: 44; height: 40; Icon { anchors.centerIn: parent; size: 12; color: theme.fg3; path: "M5 12h14" } }
                    Item {
                        width: 44; height: 40
                        Rectangle { anchors.fill: parent; color: theme.rej; opacity: xh.hovered ? 1 : 0 }
                        Icon { anchors.centerIn: parent; size: 12; color: xh.hovered ? "#ffffff" : theme.fg3; path: "M6 6l12 12M18 6L6 18" }
                        HoverHandler { id: xh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.cancel() }
                        Accessible.role: Accessible.Button; Accessible.name: "Close settings"
                    }
                }
            }

            // ---- section list ---------------------------------------------------------------------------------------------
            Rectangle {
                id: nav; x: 0; y: 40; width: 248; height: parent.height - 98; color: theme.s2
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }
                Item {
                    id: ph; width: parent.width; height: 44
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; anchors.verticalCenter: parent.verticalCenter; text: "SETTINGS"; color: theme.fg2; font.pointSize: UiState.pt(11); font.weight: Font.DemiBold; font.letterSpacing: 0.88 }
                    Item {
                        x: parent.width - 38; anchors.verticalCenter: parent.verticalCenter; width: 32; height: 32
                        Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, bh.hovered ? 0.08 : 0) }
                        Icon { anchors.centerIn: parent; size: 18; color: theme.fg; path: "M14.5 6l-6 6 6 6" }
                        HoverHandler { id: bh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.cancel() }
                        Accessible.role: Accessible.Button; Accessible.name: "Back to the library"
                    }
                }
                Rectangle {
                    id: field; x: 12; y: 44 + 12; width: parent.width - 25; height: 32; radius: 9; color: theme.base
                    border.width: 1; border.color: q.activeFocus ? theme.accent : theme.lineSoft
                    Behavior on border.color { ColorAnimation { duration: 200 } }
                    Icon { x: 10; anchors.verticalCenter: parent.verticalCenter; size: 18; color: theme.fg3; name: "search" }
                    TextInput {
                        id: q; x: 10 + 18 + 8; width: parent.width - 10 - 18 - 8 - 10; anchors.verticalCenter: parent.verticalCenter
                        color: theme.fg; font.pixelSize: 13; clip: true; selectByMouse: true
                        text: UiState.settingsQuery
                        onTextEdited: UiState.settingsQuery = text
                        Text { visible: !parent.text && !parent.activeFocus; text: "Search settings"; color: theme.fg3; font: parent.font }
                        Accessible.name: "Search settings"
                    }
                }
                // The section list scrolls in the room above the help block, so in a short window it never runs into it.
                readonly property real helpFull: 121.6
                readonly property bool roomy: height >= 44 + 12 + 32 + 4 + root.navSections.length * 42 + 20 + helpFull + 12 + 4     // else the help block is only its link
                readonly property real helpH: roomy ? helpFull : 44
                Flickable {
                    id: navFlick; objectName: "settingsNavList"
                    x: 12; y: 44 + 12 + 32 + 4; width: parent.width - 25; height: Math.max(0, nav.height - y - nav.helpH - 12 - 4)
                    contentWidth: width; contentHeight: nl.height; clip: true; boundsBehavior: Flickable.StopAtBounds; interactive: contentHeight > height
                    function reveal() {      // keep the chosen section in view
                        var top = 10 + root.navIndex * 42
                        if (top < contentY) contentY = Math.max(0, top - 10)
                        else if (top + 40 > contentY + height) contentY = Math.min(Math.max(0, contentHeight - height), top + 40 + 10 - height)
                    }
                    Connections { target: root; function onNavIndexChanged() { navFlick.reveal() } }
                    onHeightChanged: reveal()
                Item {
                    id: nl; width: parent.width; height: root.navSections.length * 42 + 20
                    AccentPill {
                        x: 0; y: 10; width: parent.width; height: 40; radius: 12
                        transform: Translate { y: root.navIndex * 42; Behavior on y { SpringAnimation { spring: 3.4; damping: 0.22; epsilon: 0.25 } } }
                        opacity: root.query.trim() !== "" ? 0 : 1
                        Behavior on opacity { NumberAnimation { duration: 300 } }
                    }
                    Repeater {
                        model: root.navSections
                        delegate: Item {
                            id: item
                            required property int index
                            required property var modelData
                            readonly property bool on: root.query.trim() === "" && modelData.id === root.section
                            readonly property int hits: root.counts[modelData.id] || 0
                            readonly property bool dirtySec: { void settings.revision; return settings.sectionDirty(modelData.id) }
                            x: 0; y: 10 + index * 42; width: parent.width; height: 40
                            opacity: root.query.trim() !== "" && hits === 0 ? 0.4 : 1
                            Rectangle { anchors.fill: parent; radius: 12; color: Qt.alpha(theme.lift, nh.hovered && !item.on ? 0.05 : 0) }
                            Icon { x: 12; anchors.verticalCenter: parent.verticalCenter; size: 18; color: item.on ? theme.accentFg : (nh.hovered ? theme.fg : theme.fg2); path: modelData.icon }
                            Text { x: 12 + 18 + 12; anchors.verticalCenter: parent.verticalCenter; text: modelData.name; font.pixelSize: 13; color: item.on || nh.hovered ? theme.fg : theme.fg2 }
                            Rectangle {   // unsaved changes in this section
                                x: parent.width - 12 - 18 - 12 - 6 + 0; anchors.verticalCenter: parent.verticalCenter; width: 6; height: 6; radius: 3; color: theme.accent
                                visible: true; opacity: item.dirtySec ? 1 : 0; scale: item.dirtySec ? 1 : 0.4
                                Behavior on opacity { NumberAnimation { duration: 250 } }
                                Behavior on scale { SpringAnimation { spring: 5; damping: 0.4; epsilon: 0.001 } }
                                Rectangle { anchors.centerIn: parent; width: 12; height: 12; radius: 6; color: Qt.alpha(theme.accent, 0.25); z: -1 }
                            }
                            Text { visible: root.query.trim() !== ""; x: parent.width - 12 - 18; width: 18; horizontalAlignment: Text.AlignHCenter; anchors.verticalCenter: parent.verticalCenter
                                   text: String(item.hits); font.pixelSize: 11; color: theme.fg3 }
                            HoverHandler { id: nh; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: { UiState.settingsSection = modelData.id; UiState.settingsQuery = ""; root.recording = "" } }
                            Accessible.role: Accessible.Button; Accessible.name: modelData.name
                        }
                    }
                }
                }
                Item {   // the help block: 1px rule, 12px padding, 12px from the bottom; positions measured from the mockup. In a short window only the link stays.
                    objectName: "settingsHelp"; x: 12; width: parent.width - 24 - 1; height: nav.helpH; y: parent.height - nav.helpH - 12
                    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                    Text { visible: nav.roomy; x: 4; y: 13; height: 16.8; verticalAlignment: Text.AlignVCenter; text: "Need a hand?"; color: theme.fg; font.pixelSize: 12; font.weight: Font.Medium }
                    Text { visible: nav.roomy; x: 4; y: 33.8; width: parent.width - 8; wrapMode: Text.WordWrap; text: "See what each setting changes and when to use it."; color: theme.fg3; font.pixelSize: 12; lineHeight: 16.8; lineHeightMode: Text.FixedHeight }
                    Text {
                        id: guideLink; objectName: "settingsGuideLink"
                        x: 4; y: nav.roomy ? 75.4 : 12; height: 20.8; verticalAlignment: Text.AlignVCenter; text: "Open the settings guide"; color: theme.accentFg; font.pixelSize: 12
                        font.underline: guideHover.hovered
                        HoverHandler { id: guideHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.openGuide() }
                        Accessible.role: Accessible.Link; Accessible.name: "Open the settings guide"
                    }
                    Text { visible: nav.roomy; x: 4; y: 106.2; height: 15.4; verticalAlignment: Text.AlignVCenter; text: "Image Triage " + settings.appVersion; color: theme.fg3; font.pixelSize: 11 }
                }
            }

            // ---- page -------------------------------------------------------------------------------------------------------
            Item {
                id: pageArea; x: 248; y: 40; width: parent.width - 248; height: parent.height - 98
                Item {
                    id: mh; width: parent.width; height: 44
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Item {
                        x: Math.max(32, (parent.width - 780) / 2); width: Math.min(parent.width - 64, 780); height: parent.height
                        Row {
                            anchors.verticalCenter: parent.verticalCenter; spacing: 10
                            Text { text: root.query.trim() !== "" ? "Search results" : root.info.title; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: -0.14 }
                            Text { text: root.query.trim() !== "" ? resultCount + (resultCount === 1 ? " setting" : " settings") + " match \"" + root.query.trim() + "\"" : "Application settings"
                                   color: theme.fg3; font.pixelSize: 13
                                   readonly property int resultCount: { var n = 0; for (var i = 0; i < root.groups.length; ++i) n += root.groups[i].rows.length; return n } }
                        }
                        Row {
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                            Rectangle {
                                height: 24; width: pt.implicitWidth + 31; radius: 12; color: theme.s2; border.color: theme.lineSoft; border.width: 1
                                anchors.verticalCenter: parent.verticalCenter
                                Row { anchors.centerIn: parent; spacing: 6
                                    Rectangle { width: 7; height: 7; radius: 4; color: settings.dirtyCount > 0 ? theme.accent : theme.win; anchors.verticalCenter: parent.verticalCenter }
                                    Text { id: pt; text: settings.dirtyCount > 0 ? settings.dirtyCount + (settings.dirtyCount === 1 ? " unsaved change" : " unsaved changes") : "All changes saved"
                                           color: theme.fg2; font.pointSize: UiState.pt(11.5) } }
                            }
                            SButton { text: "Reset to defaults"; variant: "ghost"; onClicked: settings.resetSection(root.section) }
                        }
                    }
                }
                Flickable {
                    id: scroller
                    y: 44; width: parent.width; height: parent.height - 44
                    contentWidth: width; contentHeight: colItem.height + 4 + 32
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { id: sbar; policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: sbar.active || sbar.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 300 } } } background: Item {} }
                    onContentHeightChanged: if (contentY > contentHeight - height) contentY = Math.max(0, contentHeight - height)
                    Column {
                        id: colItem
                        x: Math.max(32, (parent.width - 780) / 2); y: 4; width: Math.min(parent.width - 64, 780)
                        Text {
                            visible: root.query.trim() === "" && root.info.intro !== ""
                            topPadding: 20; bottomPadding: 0; width: Math.min(560, parent.width); wrapMode: Text.WordWrap
                            text: root.info.intro; color: theme.fg3; font.pixelSize: 13; lineHeight: 18.2; lineHeightMode: Text.FixedHeight
                        }
                        Column {
                            visible: root.query.trim() !== "" && root.groups.length === 0
                            width: parent.width; topPadding: 80; bottomPadding: 80; spacing: 4
                            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "No settings match \"" + root.query.trim() + "\""; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "Try a shorter word, such as \"preview\" or \"cache\"."; color: theme.fg3; font.pixelSize: 13 }
                        }
                        Repeater {
                            model: root.groups
                            delegate: Column {
                                required property var modelData
                                width: colItem.width
                                Item { width: 1; height: 30 }
                                Item {
                                    width: parent.width; height: (modelData.hint === "" ? 13 : 16.8) + 6        // an empty hint adds no line box
                                    Row {
                                        x: 4; spacing: 12
                                        Text { height: modelData.hint === "" ? 13 : 16.8; verticalAlignment: Text.AlignVCenter; text: modelData.title.toUpperCase(); color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                                        Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: modelData.hint; color: theme.fg3; font.pixelSize: 12 }
                                    }
                                }
                                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                                Repeater {
                                    model: modelData.rows
                                    delegate: SRow { required property var modelData; def: modelData; recording: root.recording; onRecord: (id) => root.recording = id }
                                }
                            }
                        }
                    }
                }
            }

            // ---- footer -------------------------------------------------------------------------------------------------------
            Rectangle {
                id: foot; x: 0; y: parent.height - 58; width: parent.width; height: 58; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                readonly property string conflict: { void settings.revision; return settings.conflict() }
                Row {
                    x: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                    Item {
                        width: 8; height: 8; anchors.verticalCenter: parent.verticalCenter
                        readonly property bool lit: settings.dirtyCount > 0 || settings.saved || foot.conflict !== ""
                        readonly property color tone: foot.conflict !== "" ? theme.rej : (settings.dirtyCount > 0 ? theme.accent : (settings.saved ? theme.win : theme.fg3))
                        Rectangle { anchors.centerIn: parent; width: 16; height: 16; radius: 8; color: Qt.alpha(parent.tone, settings.saved && settings.dirtyCount === 0 ? 0.22 : 0.25); visible: parent.lit }   // 0 0 0 4px halo
                        Rectangle { anchors.fill: parent; radius: 4; color: parent.tone; opacity: parent.lit ? 1 : 0.5 }
                    }
                    Text {
                        font.pixelSize: 12
                        text: foot.conflict !== "" ? foot.conflict : (settings.dirtyCount > 0 ? settings.dirtyCount + (settings.dirtyCount === 1 ? " unsaved change" : " unsaved changes") : (settings.saved ? "All changes saved" : "No unsaved changes"))
                        color: foot.conflict !== "" ? theme.rej : (settings.dirtyCount > 0 ? theme.fg : theme.fg3)
                    }
                }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: root.cancel() }
                    SButton { text: "Save changes"; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: settings.dirtyCount > 0; onClicked: settings.save() }
                }
            }
        }
    }
}
