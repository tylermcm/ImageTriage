"""The Combine folders card (the folder menu's Combine folders…): it offers a trip's camera folders, all ticked, the folder they are in
as the destination, and says what happens to the names before anything moves."""
from PySide6.QtCore import QObject

from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def test_the_card_offers_the_folders_and_previews_the_renumbering(qapp, tmp_path):
    trip = tmp_path / "Portland 10-26"
    for folder, names in (("107NZ7_2", ("_DSC1922.NEF", "_DSC1923.NEF")), ("108NZ7_2", ("_DSC0001.NEF",)), ("109NZ7_2", ("_DSC0001.NEF",))):
        (trip / folder).mkdir(parents=True)
        for name in names:
            (trip / folder / name).write_bytes(b"x")
    engine, window, warnings = _load_main(qapp, tmp_path / "app")
    window.setWidth(1300); window.setHeight(900); window.show(); pump(qapp, .4)
    card = window.findChild(QObject, "combineFoldersCard")
    assert card is not None
    card.openFor(str(trip)); pump(qapp, .4)
    assert card.property("opened") and [f["name"] for f in card.property("folders")] == ["107NZ7_2", "108NZ7_2", "109NZ7_2"]
    assert card.property("destPath") == str(trip)
    summary = window.findChild(QObject, "combineSummary")
    assert "4 files (4 photos) move into Portland 10-26" in summary.property("text") and "2 take the next numbers" in summary.property("text")
    card.toggle(2); pump(qapp, .2)                                       # 109 left out
    assert "3 files" in summary.property("text")
    card.setProperty("dest", "new"); pump(qapp, .2)
    assert window.findChild(QObject, "combineApply").property("enabled") is False       # a new folder needs a name
    card.setProperty("newName", "Portland"); pump(qapp, .2)
    assert card.property("destPath").endswith("Portland 10-26/Portland") and window.findChild(QObject, "combineApply").property("enabled")
    card.close(); pump(qapp, .3)
    assert not [w for w in warnings if "Combine" in w or "SidePanel" in w], warnings
