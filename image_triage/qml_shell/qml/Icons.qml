pragma Singleton
import QtQuick

// Stroke icons on a 24 x 24 grid, the same geometry as the HTML mockup (compact SVG path data).
QtObject {
    function circle(cx, cy, r) { return "M" + (cx - r) + " " + cy + "a" + r + " " + r + " 0 1 0 " + (2 * r) + " 0a" + r + " " + r + " 0 1 0 " + (-2 * r) + " 0"; }
    function rect(x, y, w, h, r) {
        if (!r) return "M" + x + " " + y + "h" + w + "v" + h + "h" + (-w) + "z";
        return "M" + (x + r) + " " + y + "h" + (w - 2 * r) + "a" + r + " " + r + " 0 0 1 " + r + " " + r + "v" + (h - 2 * r)
            + "a" + r + " " + r + " 0 0 1 " + (-r) + " " + r + "h" + (-(w - 2 * r)) + "a" + r + " " + r + " 0 0 1 " + (-r) + " " + (-r)
            + "v" + (-(h - 2 * r)) + "a" + r + " " + r + " 0 0 1 " + r + " " + (-r) + "z";
    }

    readonly property string folder: "M3.5 7.5a2 2 0 012-2h4l2 2.5h7a2 2 0 012 2v7.5a2 2 0 01-2 2h-13a2 2 0 01-2-2z"
    readonly property string star: "M12 4l2.4 5 5.4.6-4 3.7 1.1 5.4L12 16l-4.9 2.7L8.2 13.3l-4-3.7 5.4-.6z"
    readonly property string gear: "M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"

    readonly property var paths: ({
        library: folder,
        faces: circle(12, 12, 8.5) + circle(9, 10.5, 0.6) + circle(15, 10.5, 0.6) + "M8.8 14.5c1 1.3 2.2 1.9 3.2 1.9s2.2-.6 3.2-1.9",
        collections: "M12 4l8.5 4.5L12 13 3.5 8.5z" + "M3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5",
        pocketdrop: rect(7, 3.5, 10, 17, 2.5) + "M12 8.5v5M9.8 11.2L12 8.5l2.2 2.7",
        settings: circle(12, 12, 3) + gear,
        search: circle(11, 11, 6.5) + "M16 16l4 4",
        chevL: "M14.5 6l-6 6 6 6", chevR: "M9.5 6l6 6-6 6", chevUp: "M6 14l6-6 6 6", chevDown: "M6 10l6 6 6-6",
        check: "M4.5 12.5l4.5 4.5 10.5-10.5", x: "M6 6l12 12M18 6L6 18", minus: "M5 12h14", sliders: "M4 7h10M18 7h2M4 17h2M10 17h10" + circle(16, 7, 2) + circle(8, 17, 2), plus: "M12 5v14M5 12h14",
        star: star,
        undo: "M9 7L4.5 11.5 9 16M5 11.5h8a6 6 0 016 6",
        openFolder: "M3.5 8a2 2 0 012-2h4l2 2.5h7a2 2 0 012 2V17a2 2 0 01-2 2h-13a2 2 0 01-2-2z",
        view: rect(4, 4, 7, 7, 1.5) + rect(13, 4, 7, 7, 1.5) + rect(4, 13, 7, 7, 1.5) + rect(13, 13, 7, 7, 1.5),
        funnel: "M4 5.5h16l-6.2 7.3v5.2l-3.6 1.8v-7z",
        accept: circle(12, 12, 8.5) + "M8.5 12.3l2.4 2.4 4.6-5",
        reject: circle(12, 12, 8.5) + "M9 9l6 6M15 9l-6 6",
        sort: "M7 5v14M7 19l-3-3M7 19l3-3M17 19V5M17 5l-3 3M17 5l3 3",
        columns: rect(3.5, 4.5, 17, 15, 2) + "M9.2 4.5v15M14.8 4.5v15",
        compare: rect(3.5, 5, 7.5, 14, 1.5) + rect(13, 5, 7.5, 14, 1.5),
        eye: "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" + circle(12, 12, 2.8),
        ladder: "M4.5 19.5h15M6 19.5V15h3.5v4.5M10.5 19.5v-9H14v9M15 19.5V6h3.5v13.5",
        tag: "M4 12.5V5.5h7l8.5 8.5-7 7z" + circle(8.5, 9.5, 0.8),
        convert: "M4 9h12l-3-3M20 15H8l3 3",
        editor: "M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z",
        palette: "M4.5 7.5l5 4.5-5 4.5M12 17h7.5",
        keyboard: rect(2.5, 6.5, 19, 11, 2.5) + "M6.5 10.5h.01M10 10.5h.01M13.5 10.5h.01M17 10.5h.01M7.5 14h9",
        newfolder: folder + "M12 11v5M9.5 13.5h5",
        sun: circle(12, 12, 4) + "M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4",
        inspector: rect(3.5, 4.5, 17, 15, 3) + "M14.5 4.5v15",
        spark: "M12 3.5l1.8 4.7 4.7 1.8-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.8zM18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z",
        winMin: "M5 12h14", winMax: rect(5, 5, 14, 14, 1), winClose: "M6 6l12 12M18 6L6 18",
        densS: rect(4, 4, 4, 4) + rect(10, 4, 4, 4) + rect(16, 4, 4, 4) + rect(4, 10, 4, 4) + rect(10, 10, 4, 4) + rect(16, 10, 4, 4) + rect(4, 16, 4, 4) + rect(10, 16, 4, 4) + rect(16, 16, 4, 4),
        densM: rect(4, 4, 7, 7) + rect(13, 4, 7, 7) + rect(4, 13, 7, 7) + rect(13, 13, 7, 7),
        densL: rect(4, 5, 16, 14)
    })
}
