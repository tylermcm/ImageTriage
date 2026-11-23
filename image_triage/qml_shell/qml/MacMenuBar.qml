import QtQuick
import Qt.labs.platform as Platform

// macOS: the menus in the menu bar at the top of the screen, where Mac apps keep them, instead of in the window's top bar (TopBar hides
// its File, Review, View, AI and Help there). The rows are TopMenus' own (`source.build(name)`) and run the same commands
// (`source.run(id)`); the Help menu is reference_model's `view.menuItems`. Each menu is filled again as it opens, so the recent folders
// and the ticks are current. Settings… and Exit move to the app's own menu (Image Triage > Settings…, Quit): Qt's text heuristics.
// Shortcuts are not set on the rows: the window's own shortcuts already run them, and a single-key one (W, K, R) set on a menu item
// would take the key from every text field.
Platform.MenuBar {
    id: bar
    property var source                                     // TopMenus

    Component { id: itemC; Platform.MenuItem {} }
    Component { id: sepC; Platform.MenuSeparator {} }
    Component { id: menuC; Platform.Menu {} }

    function fill(menu, rows) {
        menu.clear()
        for (var i = 0; i < rows.length; ++i) {
            var r = rows[i]
            if (r.kind === "sep") {
                menu.addItem(sepC.createObject(menu))
            } else if (r.kind === "head") {
                menu.addItem(itemC.createObject(menu, { text: r.label, enabled: false }))
            } else if (r.kind === "sub") {
                var sub = menuC.createObject(menu, { title: r.label })
                fill(sub, r.items || [])
                menu.addMenu(sub)
            } else {
                var item = itemC.createObject(menu, { text: r.label, enabled: !r.off && !!r.id, checkable: !!r.on, checked: r.on ? !!r.on() : false })
                item.triggered.connect(bar.trigger.bind(bar, r))
                menu.addItem(item)
            }
        }
    }
    function trigger(row) {
        if (row.off) toasts.show("warn", row.label.replace(/…$/, "") + " isn’t connected yet", "Nothing was changed.")
        else bar.source.run(row.id)
    }
    function fillHelp(menu) {
        menu.clear()
        var rows = reference.view.menuItems || []
        for (var i = 0; i < rows.length; ++i) {
            if (rows[i].sep) { menu.addItem(sepC.createObject(menu)); continue }
            var item = itemC.createObject(menu, { text: rows[i].label })
            item.triggered.connect(reference.open.bind(reference, rows[i].id))
            menu.addItem(item)
        }
    }

    Platform.Menu { id: fileMenu; title: "File"; onAboutToShow: bar.fill(fileMenu, bar.source.build("file")) }
    Platform.Menu { id: reviewMenu; title: "Review"; onAboutToShow: bar.fill(reviewMenu, bar.source.build("review")) }
    Platform.Menu { id: viewMenu; title: "View"; onAboutToShow: bar.fill(viewMenu, bar.source.build("view")) }
    Platform.Menu { id: aiMenu; title: "AI"; onAboutToShow: bar.fill(aiMenu, bar.source.build("ai")) }
    Platform.Menu { id: helpMenu; title: "Help"; onAboutToShow: bar.fillHelp(helpMenu) }

    // Filled once at the start too: macOS shows a menu's items as it opens, and Settings… and Exit must already be in the app menu.
    Component.onCompleted: {
        fill(fileMenu, source.build("file")); fill(reviewMenu, source.build("review")); fill(viewMenu, source.build("view"))
        fill(aiMenu, source.build("ai")); fillHelp(helpMenu)
    }
}
