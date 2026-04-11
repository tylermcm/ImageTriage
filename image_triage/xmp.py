from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET

from .formats import RAW_SUFFIXES, suffix_for_path
from .models import ImageRecord, SessionAnnotation

X_NS = "adobe:ns:meta/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XMP_NS = "http://ns.adobe.com/xap/1.0/"
DC_NS = "http://purl.org/dc/elements/1.1/"
TRIAGE_NS = "https://image-triage.app/ns/1.0/"

XMPMETA = f"{{{X_NS}}}xmpmeta"
RDF = f"{{{RDF_NS}}}RDF"
DESCRIPTION = f"{{{RDF_NS}}}Description"
ABOUT = f"{{{RDF_NS}}}about"
LI = f"{{{RDF_NS}}}li"
BAG = f"{{{RDF_NS}}}Bag"
RATING = f"{{{XMP_NS}}}Rating"
LABEL = f"{{{XMP_NS}}}Label"
SUBJECT = f"{{{DC_NS}}}subject"
REVIEW_STATE = f"{{{TRIAGE_NS}}}ReviewState"
PHOTOSHOP = f"{{{TRIAGE_NS}}}Photoshop"
REVIEW_ROUND = f"{{{TRIAGE_NS}}}ReviewRound"

ACCEPTED_LABELS = frozenset({"accept", "accepted", "keep", "pick", "picked", "select", "selected", "winner"})
REJECTED_LABELS = frozenset({"reject", "rejected"})

ET.register_namespace("x", X_NS)
ET.register_namespace("rdf", RDF_NS)
ET.register_namespace("xmp", XMP_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("triage", TRIAGE_NS)


def load_sidecar_annotations(records: list[ImageRecord]) -> dict[str, SessionAnnotation]:
    loaded: dict[str, SessionAnnotation] = {}
    for record in records:
        annotation = load_sidecar_annotation(record.path)
        if annotation.is_empty:
            continue
        loaded[record.path] = annotation
    return loaded


def load_sidecar_annotation(image_path: str) -> SessionAnnotation:
    for candidate in _sidecar_candidates(image_path):
        if not os.path.exists(candidate):
            continue
        annotation = _read_single_sidecar(candidate)
        if annotation.is_empty:
            continue
        return annotation
    return SessionAnnotation()


def sync_sidecar_annotation(record: ImageRecord, annotation: SessionAnnotation | None) -> None:
    effective = annotation or SessionAnnotation()
    paths = existing_sidecar_paths(record.path)
    if not paths:
        if effective.is_empty:
            return
        paths = (_preferred_sidecar_path(record.path),)
    for path in paths:
        _write_single_sidecar(path, effective)


def existing_sidecar_paths(image_path: str) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in _sidecar_candidates(image_path):
        normalized = os.path.normpath(candidate).casefold()
        if normalized in seen or not os.path.exists(candidate):
            continue
        seen.add(normalized)
        ordered.append(os.path.normpath(candidate))
    return tuple(ordered)


def sidecar_bundle_paths(record: ImageRecord) -> tuple[str, ...]:
    return existing_sidecar_paths(record.path)


def _read_single_sidecar(sidecar_path: str) -> SessionAnnotation:
    try:
        tree = ET.parse(sidecar_path)
    except (ET.ParseError, OSError):
        return SessionAnnotation()

    descriptions = tree.getroot().findall(f".//{DESCRIPTION}")
    rating_value = ""
    label_value = ""
    review_state = ""
    photoshop_flag = False
    review_round = ""
    tags: tuple[str, ...] = ()

    for description in descriptions:
        rating_value = rating_value or _field_text(description, RATING)
        label_value = label_value or _field_text(description, LABEL)
        review_state = review_state or _field_text(description, REVIEW_STATE)
        photoshop_flag = photoshop_flag or _parse_bool(_field_text(description, PHOTOSHOP))
        review_round = review_round or _field_text(description, REVIEW_ROUND)
        if not tags:
            tags = _read_bag(description, SUBJECT)

    rating = _parse_rating(rating_value)
    winner, reject = _resolve_review_state(review_state, label_value, rating)
    return SessionAnnotation(
        winner=winner,
        reject=reject,
        photoshop=photoshop_flag,
        rating=max(0, rating),
        tags=tags,
        review_round=review_round,
    )


def _write_single_sidecar(sidecar_path: str, annotation: SessionAnnotation) -> None:
    tree = _load_sidecar_tree(sidecar_path)
    root = tree.getroot()
    rdf = root.find(RDF)
    if rdf is None:
        rdf = ET.SubElement(root, RDF)
    description = _ensure_description(rdf)

    _set_field(description, REVIEW_STATE, _review_state_value(annotation))
    _set_field(description, PHOTOSHOP, "true" if annotation.photoshop else "")
    _set_field(description, REVIEW_ROUND, annotation.review_round.strip())
    _set_rating(description, annotation.rating)
    _set_tags(description, annotation.tags)
    _sync_label_field(description, annotation)

    _prune_empty_descriptions(rdf)
    if not _document_has_payload(root):
        try:
            os.remove(sidecar_path)
        except FileNotFoundError:
            return
        return

    ET.indent(tree, space="  ")
    _atomic_write(sidecar_path, tree)


def _sidecar_candidates(image_path: str) -> tuple[str, ...]:
    normalized = os.path.normpath(image_path)
    source = Path(normalized)
    suffix = suffix_for_path(normalized)
    collapsed = os.path.normpath(str(source.with_suffix(".xmp")))
    appended = os.path.normpath(f"{normalized}.xmp")

    ordered: list[str] = []
    if suffix in RAW_SUFFIXES:
        ordered.extend([collapsed, appended])
    else:
        ordered.extend([appended, collapsed])

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in ordered:
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return tuple(deduped)


def _preferred_sidecar_path(image_path: str) -> str:
    return _sidecar_candidates(image_path)[0]


def _load_sidecar_tree(sidecar_path: str) -> ET.ElementTree:
    try:
        return ET.parse(sidecar_path)
    except (ET.ParseError, OSError):
        pass
    root = ET.Element(XMPMETA)
    rdf = ET.SubElement(root, RDF)
    ET.SubElement(rdf, DESCRIPTION, {ABOUT: ""})
    return ET.ElementTree(root)


def _ensure_description(rdf: ET.Element) -> ET.Element:
    description = rdf.find(DESCRIPTION)
    if description is None:
        description = ET.SubElement(rdf, DESCRIPTION, {ABOUT: ""})
    elif ABOUT not in description.attrib:
        description.set(ABOUT, "")
    return description


def _field_text(description: ET.Element, name: str) -> str:
    value = description.get(name, "")
    if value:
        return value.strip()
    child = description.find(name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _set_field(description: ET.Element, name: str, value: str) -> None:
    child = description.find(name)
    if value:
        description.set(name, value)
        if child is not None:
            description.remove(child)
        return
    description.attrib.pop(name, None)
    if child is not None:
        description.remove(child)


def _parse_rating(value: str) -> int:
    if not value:
        return 0
    try:
        return int(round(float(value)))
    except ValueError:
        return 0


def _parse_bool(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _resolve_review_state(review_state: str, label: str, rating: int) -> tuple[bool, bool]:
    normalized_state = review_state.strip().casefold()
    if normalized_state in {"accepted", "winner"}:
        return True, False
    if normalized_state in {"rejected", "reject"}:
        return False, True

    normalized_label = label.strip().casefold()
    if normalized_label in ACCEPTED_LABELS:
        return True, False
    if normalized_label in REJECTED_LABELS:
        return False, True

    if rating < 0:
        return False, True
    return False, False


def _review_state_value(annotation: SessionAnnotation) -> str:
    if annotation.winner:
        return "accepted"
    if annotation.reject:
        return "rejected"
    return ""


def _set_rating(description: ET.Element, rating: int) -> None:
    if rating > 0:
        _set_field(description, RATING, str(max(0, min(5, int(rating)))))
        return
    _set_field(description, RATING, "")


def _read_bag(description: ET.Element, field_name: str) -> tuple[str, ...]:
    field = description.find(field_name)
    if field is None:
        return ()
    bag = field.find(BAG)
    if bag is None:
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for item in bag.findall(LI):
        text = (item.text or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return tuple(ordered)


def _set_tags(description: ET.Element, tags: tuple[str, ...]) -> None:
    normalized_tags = _normalize_tags(tags)
    field = description.find(SUBJECT)
    if not normalized_tags:
        if field is not None:
            description.remove(field)
        return

    if field is None:
        field = ET.SubElement(description, SUBJECT)
    bag = field.find(BAG)
    if bag is None:
        for child in list(field):
            field.remove(child)
        bag = ET.SubElement(field, BAG)
    else:
        for child in list(bag):
            bag.remove(child)
    for tag in normalized_tags:
        item = ET.SubElement(bag, LI)
        item.text = tag


def _normalize_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        text = str(tag).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return tuple(ordered)


def _sync_label_field(description: ET.Element, annotation: SessionAnnotation) -> None:
    existing = _field_text(description, LABEL)
    existing_is_managed = existing.strip().casefold() in ACCEPTED_LABELS | REJECTED_LABELS
    desired = ""
    if annotation.winner:
        desired = "Accepted"
    elif annotation.reject:
        desired = "Rejected"

    if desired:
        if not existing or existing_is_managed:
            _set_field(description, LABEL, desired)
        return

    if existing_is_managed:
        _set_field(description, LABEL, "")


def _prune_empty_descriptions(rdf: ET.Element) -> None:
    for description in list(rdf.findall(DESCRIPTION)):
        attrs = {key: value for key, value in description.attrib.items() if key != ABOUT and value.strip()}
        children = [child for child in list(description) if _element_has_payload(child)]
        for child in list(description):
            if child not in children:
                description.remove(child)
        if attrs or children:
            continue
        rdf.remove(description)


def _document_has_payload(root: ET.Element) -> bool:
    for description in root.findall(f".//{DESCRIPTION}"):
        if any(key != ABOUT and value.strip() for key, value in description.attrib.items()):
            return True
        if any(_element_has_payload(child) for child in list(description)):
            return True
    return False


def _element_has_payload(element: ET.Element) -> bool:
    text = (element.text or "").strip()
    if text:
        return True
    if any(value.strip() for value in element.attrib.values()):
        return True
    return any(_element_has_payload(child) for child in list(element))


def _atomic_write(sidecar_path: str, tree: ET.ElementTree) -> None:
    destination = Path(sidecar_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile("wb", delete=False, dir=destination.parent, prefix=f"{destination.name}.", suffix=".tmp") as handle:
            temp_path = Path(handle.name)
            tree.write(handle, encoding="utf-8", xml_declaration=True)
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
