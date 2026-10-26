import QtQuick

// A popover's small-caps heading: 10.5px, 600, 0.08em; 6px above, 4px below (23px in all).
Item {
    id: ph
    property string caption
    width: parent ? parent.width : 200
    height: 23
    Text {
        x: 10; y: 6; height: 13; verticalAlignment: Text.AlignVCenter
        text: ph.caption.toUpperCase(); color: theme.fg3
        font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84
    }
}
