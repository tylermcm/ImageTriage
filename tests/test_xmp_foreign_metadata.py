"""A decision written to a sidecar another program made must not erase what that program wrote."""
from __future__ import annotations

from image_triage.models import ImageRecord, SessionAnnotation
from image_triage.xmp import load_sidecar_annotation, sync_sidecar_annotation

LIGHTROOM = '''<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" xmp:Rating="4" xmp:Label="Red" crs:Exposure2012="+0.35">
   <dc:subject><rdf:Bag><rdf:li>Georgia</rdf:li><rdf:li>client-A</rdf:li></rdf:Bag></dc:subject>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''


def _photo(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9")
    sidecar = tmp_path / "a.jpg.xmp"
    sidecar.write_text(LIGHTROOM, encoding="utf-8")
    return ImageRecord(path=str(photo), name="a.jpg", size=4, modified_ns=1), sidecar


def test_marking_a_winner_keeps_the_rating_keywords_label_and_develop_settings(tmp_path):
    record, sidecar = _photo(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation(winner=True), None)      # the app showed no rating and no tags for it
    text = sidecar.read_text(encoding="utf-8")
    read = load_sidecar_annotation(record.path)
    assert read.winner and read.rating == 4 and read.tags == ("Georgia", "client-A")
    assert 'Label="Red"' in text and "Exposure2012" in text
    assert text.startswith("<?xpacket begin=") and text.rstrip().endswith('<?xpacket end="w"?>')


def test_a_rating_the_user_sets_is_written_and_one_they_clear_is_removed(tmp_path):
    record, _sidecar = _photo(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation(rating=2), None)
    assert load_sidecar_annotation(record.path).rating == 2
    sync_sidecar_annotation(record, SessionAnnotation(), SessionAnnotation(rating=2))
    read = load_sidecar_annotation(record.path)
    assert read.rating == 0 and read.tags == ("Georgia", "client-A")


def test_the_apps_own_tag_is_added_and_removed_without_touching_the_others(tmp_path):
    record, _sidecar = _photo(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation(tags=("keep",)), None)
    assert load_sidecar_annotation(record.path).tags == ("Georgia", "client-A", "keep")
    sync_sidecar_annotation(record, SessionAnnotation(reject=True), SessionAnnotation(tags=("keep",)))
    read = load_sidecar_annotation(record.path)
    assert read.reject and read.tags == ("Georgia", "client-A") and read.rating == 4


def test_a_sidecar_the_app_creates_holds_the_whole_annotation(tmp_path):
    photo = tmp_path / "b.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9")
    record = ImageRecord(path=str(photo), name="b.jpg", size=4, modified_ns=1)
    sync_sidecar_annotation(record, SessionAnnotation(winner=True, rating=5, tags=("keep",)), SessionAnnotation(rating=5))
    read = load_sidecar_annotation(record.path)
    assert read.winner and read.rating == 5 and read.tags == ("keep",)
