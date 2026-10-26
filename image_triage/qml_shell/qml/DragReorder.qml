import QtQuick

// Drag a button along its row or column to put it somewhere else among the buttons beside it (the ones with a `dragId`). Declare one inside the
// button. Nothing is moved in the row or column itself: each button is drawn shifted to the slot its place in `UiState.<name>Order` gives it, so
// the buttons keep their sizes, menus and clicks. The button follows the pointer; when it passes the middle of a neighbour they swap places, and
// the order is remembered. Plain clicks are untouched: nothing happens until the pointer has moved a few pixels.
Item {
    id: dr
    anchors.fill: parent                              // the handlers below act on this item, so it covers the button
    property Item button: parent.parent               // the item that moves (this sits in the button's content holder)
    property bool horizontal: false
    property string orderName                         // "rail" or "bar": which UiState.<name>Order holds the order of this row or column
    property real offset: 0                           // how far the pointer has pulled the button from its slot
    property real carried: 0                          // how much of the pointer's travel the swaps already account for
    readonly property bool active: handler.active

    // where the slot of this button is, as a shift from where its row or column laid it out (re-evaluated when the order or the layout changes)
    property real shift: {
        void UiState[orderName + "Order"]; void button.parent.childrenRect.width; void button.parent.childrenRect.height; void button.visible
        var p = positions()
        for (var i = 0; i < p.length; ++i) if (p[i].item === button) return p[i].pos - start(button)
        return 0
    }
    Behavior on shift { enabled: !dr.active; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    Translate { id: tr; x: dr.horizontal ? dr.shift + dr.offset : 0; y: dr.horizontal ? 0 : dr.shift + dr.offset }
    NumberAnimation { id: settle; target: dr; property: "offset"; to: 0; duration: 170; easing.type: Easing.OutCubic; onFinished: dr.button.z = 0 }

    function size(it) { return horizontal ? it.width : it.height }
    function start(it) { return horizontal ? it.x : it.y }
    function peers() {                                // the buttons that can be moved, in the order their row or column lays them out
        var out = [], kids = button.parent.children
        for (var i = 0; i < kids.length; ++i) if (kids[i].dragId !== undefined && kids[i].dragId !== "" && kids[i].visible) out.push(kids[i])
        return out
    }
    function ranked() {                               // the same buttons in the order they are to be shown in
        var list = peers(), saved = UiState[orderName + "Order"] || []
        return list.map(function (it, i) { var r = saved.indexOf(it.dragId); return { item: it, rank: r < 0 ? 100000 + i : r } })
                   .sort(function (a, b) { return a.rank - b.rank }).map(function (e) { return e.item })
    }
    function positions() {                            // where each button starts, once they are in the order to be shown in
        var list = peers(), shown = ranked(), spacing = button.parent.spacing || 0, out = []
        if (!list.length) return out
        var pos = start(list[0])
        for (var i = 0; i < shown.length; ++i) { out.push({ item: shown[i], pos: pos }); pos += size(shown[i]) + spacing }
        return out
    }

    function step(pointer) {
        offset = pointer - carried
        var p = positions(), at = -1
        for (var i = 0; i < p.length; ++i) if (p[i].item === button) at = i
        if (at < 0) return
        var centre = p[at].pos + size(button) / 2 + offset, other = -1
        if (at < p.length - 1 && centre > p[at + 1].pos + size(p[at + 1].item) / 2) other = at + 1
        else if (at > 0 && centre < p[at - 1].pos + size(p[at - 1].item) / 2) other = at - 1
        if (other < 0) return
        var before = p[at].pos
        var ids = p.map(function (e) { return e.item.dragId })
        var moved = ids[at]; ids[at] = ids[other]; ids[other] = moved
        UiState[orderName + "Order"] = ids
        var q = positions()
        for (var j = 0; j < q.length; ++j) if (q[j].item === button) carried += q[j].pos - before
        offset = pointer - carried
    }

    DragHandler {
        id: handler
        target: null
        xAxis.enabled: dr.horizontal; yAxis.enabled: !dr.horizontal
        dragThreshold: 8
        enabled: dr.orderName === "bar" || !UiState.pinsLocked        // the lock in the "+" menu holds the rail still
        cursorShape: active ? Qt.ClosedHandCursor : Qt.ArrowCursor
        onActiveChanged: {
            if (active) { settle.stop(); dr.carried = 0; dr.button.z = 50 }
            else { settle.start(); uiMemory.set(dr.orderName + "Order", dr.ranked().map(function (it) { return it.dragId })) }
        }
        onTranslationChanged: if (active) dr.step(dr.horizontal ? translation.x : translation.y)
    }
    Component.onCompleted: button.transform = [tr]
}
