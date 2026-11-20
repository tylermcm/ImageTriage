import QtQuick

// The fix for an AI tool's error, beside it (ai_fix.py): "Repair Image Triage" when a file of the app is missing, "Open AI Center" when
// the AI runtime or a model is not set up. Hidden when the error has no fix to offer.
Column {
    id: fix
    property string error
    property var raw                                   // the RAW editor (fixKind, fixLabel, repairApp)
    readonly property string kind: raw && error ? raw.fixKind(error) : ""
    property string note
    visible: kind !== ""
    spacing: 4
    onErrorChanged: note = ""
    SButton {
        objectName: "aiFixButton"
        text: fix.raw && fix.error ? fix.raw.fixLabel(fix.error) : ""
        variant: "primary"; h: 28; hpad: 11; radius: 9
        onClicked: {
            if (fix.kind === "aiCenter") { cull.openCenter(); return }
            var why = fix.raw.repairApp()
            fix.note = why || "Windows is putting the missing files back. Restart Image Triage when it is done."
        }
    }
    Text { visible: fix.note.length > 0; width: fix.width; wrapMode: Text.WordWrap; text: fix.note; color: theme.fg3; font.pixelSize: 11 }
}
