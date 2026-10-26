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
    readonly property string gear: "M 19.4 15a 1.65 1.65 0 0 0 .33 1.82l .06 .06a 2 2 0 0 1 0 2.83a 2 2 0 0 1 -2.83 0l -.06 -.06a 1.65 1.65 0 0 0 -1.82 -.33a 1.65 1.65 0 0 0 -1 1.51V 21a 2 2 0 0 1 -2 2a 2 2 0 0 1 -2 -2v -.09A 1.65 1.65 0 0 0 9 19.4a 1.65 1.65 0 0 0 -1.82 .33l -.06 .06a 2 2 0 0 1 -2.83 0a 2 2 0 0 1 0 -2.83l .06 -.06a 1.65 1.65 0 0 0 .33 -1.82a 1.65 1.65 0 0 0 -1.51 -1H 3a 2 2 0 0 1 -2 -2a 2 2 0 0 1 2 -2h .09A 1.65 1.65 0 0 0 4.6 9a 1.65 1.65 0 0 0 -.33 -1.82l -.06 -.06a 2 2 0 0 1 0 -2.83a 2 2 0 0 1 2.83 0l .06 .06a 1.65 1.65 0 0 0 1.82 .33H 9a 1.65 1.65 0 0 0 1 -1.51V 3a 2 2 0 0 1 2 -2a 2 2 0 0 1 2 2v .09a 1.65 1.65 0 0 0 1 1.51a 1.65 1.65 0 0 0 1.82 -.33l .06 -.06a 2 2 0 0 1 2.83 0a 2 2 0 0 1 0 2.83l -.06 .06a 1.65 1.65 0 0 0 -.33 1.82V 9a 1.65 1.65 0 0 0 1.51 1H 21a 2 2 0 0 1 2 2a 2 2 0 0 1 -2 2h -.09a 1.65 1.65 0 0 0 -1.51 1z"

    // Where each icon's ink sits relative to the middle of its 24 x 24 box (measured from the rendered paths), so Icon can centre it: a folder or
    // a chevron drawn off-centre in its box looked off-centre in its button.
    readonly property var inkOffset: ({ library: [0.0, 0.5], collections: [0.0, 0.5], search: [0.25, 0.25], chevL: [-0.5, 0.0], chevR: [0.5, 0.0], chevUp: [0.0, -1.0], chevDown: [0.0, 1.0], check: [0.0, -0.25], star: [0.0, -0.65], undo: [-0.25, 0.25], openFolder: [0.0, 0.5], funnel: [0.0, 0.65], ladder: [0.0, 0.75], tag: [-0.25, 1.25], editor: [0.0, -0.55], palette: [0.0, 0.25], newfolder: [0.0, 0.5], spark: [1.4, 0.3], heart: [0.0, 0.98], people: [-0.25, 0.0], bars: [0.0, 0.5] })

    readonly property var rawPaths: ({
        library: folder,
        faces: circle(12, 12, 8.5) + circle(9, 10.5, 0.6) + circle(15, 10.5, 0.6) + "M8.8 14.5c1 1.3 2.2 1.9 3.2 1.9s2.2-.6 3.2-1.9",
        collections: "M12 4l8.5 4.5L12 13 3.5 8.5z" + "M3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5",
        pocketdrop: rect(7, 3.5, 10, 17, 2.5) + "M12 8.5v5M9.8 11.2L12 8.5l2.2 2.7",
        settings: circle(12, 12, 3) + gear,
        search: circle(11, 11, 6.5) + "M16 16l4 4",
        lock: "M7 11V8a5 5 0 0110 0v3M5 11h14v10H5zM12 15v2", unlock: "M7 11V8a5 5 0 019.6-2M5 11h14v10H5zM12 15v2",
        arrowUp: "M12 19V5M6 11l6-6 6 6",
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
        winMin: "M5 12h14", winMax: rect(5, 5, 14, 14, 1), winRestore: rect(4.5, 8.5, 11, 11, 1) + "M8.5 8.5v-3a1 1 0 011-1h9a1 1 0 011 1v9a1 1 0 01-1 1h-3", winClose: "M6 6l12 12M18 6L6 18",
        densS: rect(4, 4, 4, 4) + rect(10, 4, 4, 4) + rect(16, 4, 4, 4) + rect(4, 10, 4, 4) + rect(10, 10, 4, 4) + rect(16, 10, 4, 4) + rect(4, 16, 4, 4) + rect(10, 16, 4, 4) + rect(16, 16, 4, 4),
        densM: rect(4, 4, 7, 7) + rect(13, 4, 7, 7) + rect(4, 13, 7, 7) + rect(13, 13, 7, 7),
        densL: rect(4, 5, 16, 14),
        heart: "M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0112 7.4a4.3 4.3 0 017.5 2.4C19.5 15.4 12 20 12 20z",
        more: "M5 12h.01M12 12h.01M19 12h.01",
        people: "M16 11a3 3 0 100-6 3 3 0 000 6zM8 12a3 3 0 100-6 3 3 0 000 6zM2.5 19c.4-2.6 2.6-4.2 5.5-4.2s5.1 1.6 5.5 4.2M14 15.2c.7-.3 1.4-.4 2-.4 2.5 0 4.5 1.5 5 4",
        bars: "M7 19v-6M12 19V6M17 19v-9",
        focusAssist: "M8 4H5a1 1 0 0 0 -1 1v3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1 -1 1h-3M8 20H5a1 1 0 0 1 -1 -1v-3" + circle(12, 12, 3),
        loupe: circle(10.5, 10.5, 6.5) + "M15.2 15.2L20 20M10.5 7.5v6M7.5 10.5h6",
        movePhoto: "M3 8V6a1 1 0 0 1 1 -1h5l2 3h9a1 1 0 0 1 1 1v9a1 1 0 0 1 -1 1H4a1 1 0 0 1 -1 -1V8zM8 13h8M13 10l3 3 -3 3",
        photoTag: "M4 4h7l9 9a1.4 1.4 0 0 1 0 2l-5 5a1.4 1.4 0 0 1 -2 0l-9 -9V4z" + circle(8, 8, 1),
        addCollection: rect(8, 4, 12, 12, 2) + "M4 8v10a2 2 0 0 0 2 2h10M14 7v6M11 10h6",
        trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1 -13M10 10.5v6M14 10.5v6"
    })

    // Qt's SVG path reader mis-reads arc flags written without a separator ("a2 2 0 012-2" means flags 0 and 1, then 2 and -2), which bent the gear
    // and the folder. Every path is rewritten once with explicit commands and spaced flags before it is drawn.
    function spaced(d) {
        var counts = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0 }
        var num = /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i
        var i = 0, cmd = "", out = []
        function skip() { while (i < d.length && (d[i] === " " || d[i] === ",")) ++i }
        function read() { skip(); var m = num.exec(d.slice(i)); i += m[0].length; return m[0] }
        function flag() { skip(); return d[i++] }
        while (true) {
            skip()
            if (i >= d.length) break
            if (/[a-zA-Z]/.test(d[i])) cmd = d[i++]
            var up = cmd.toUpperCase()
            if (up === "Z") { out.push(cmd); continue }
            var a = []
            if (up === "A") a = [read(), read(), read(), flag(), flag(), read(), read()]
            else for (var k = 0; k < counts[up]; ++k) a.push(read())
            out.push(cmd + " " + a.join(" "))
            if (cmd === "M") cmd = "L"; else if (cmd === "m") cmd = "l"
        }
        return out.join("")
    }
    readonly property var paths: { var o = {}; for (var k in rawPaths) o[k] = spaced(rawPaths[k]); return o }
}
