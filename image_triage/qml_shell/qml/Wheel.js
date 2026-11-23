.pragma library
.import QtQuick as Q

// Wheel input that behaves the same on Windows, macOS and Linux.
//
// A trackpad (and a Magic Mouse) scrolls continuously: its events carry a scroll phase or come from a touchpad, and their pixel
// distance is the gesture itself. A wheel mouse scrolls in notches (angleDelta, 120 a notch); macOS also gives each notch a small
// pixel distance, which taken as the gesture made a notch move a few pixels instead of a row.

// Every WheelHandler takes these devices: Qt's default is the mouse alone, so a Mac trackpad scrolled nothing.
var devices = Q.PointerDevice.Mouse | Q.PointerDevice.TouchPad

// Continuous: pixels that are the gesture itself. A notch always has an angle; pixels without one, a scroll phase, or a touchpad are a
// gesture's.
function continuous(e) {
    if (e.pixelDelta.y === 0) return false
    return e.angleDelta.y === 0 || e.phase !== Qt.NoScrollPhase
            || (e.device !== null && e.device !== undefined && e.device.type === Q.PointerDevice.TouchPad)
}

// The gesture's pixels, or 0 for a notch (the caller works out a notch's distance from angleDelta).
function pixels(e) { return continuous(e) ? e.pixelDelta.y : 0 }

// Nothing to do: a gesture's begin or end, which carries no distance.
function empty(e) { return e.angleDelta.y === 0 && e.pixelDelta.y === 0 && e.angleDelta.x === 0 && e.pixelDelta.x === 0 }

// Zoom steps (a notch is one): a gesture's pixels count 60 to a step.
function steps(e) { return continuous(e) ? e.pixelDelta.y / 60 : e.angleDelta.y / 120 }
