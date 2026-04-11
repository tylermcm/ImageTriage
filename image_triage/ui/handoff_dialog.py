from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..image_convert import convert_formats
from ..image_resize import resize_presets
from ..production_workflows import (
    RECIPE_CONTENT_BUNDLE,
    RECIPE_CONTENT_EXPORT,
    RECIPE_TRANSFER_ARCHIVE,
    RECIPE_TRANSFER_COPY,
    RECIPE_TRANSFER_MOVE,
    WorkflowRecipe,
    recipe_key_for_name,
    recipe_summary_lines,
)


@dataclass(slots=True, frozen=True)
class HandoffDialogResult:
    recipe: WorkflowRecipe
    destination_root: str


class HandoffBuilderDialog(QDialog):
    def __init__(
        self,
        *,
        built_in_recipes: tuple[WorkflowRecipe, ...],
        saved_recipes: tuple[WorkflowRecipe, ...],
        default_destination_root: str,
        selection_count: int,
        initial_recipe: WorkflowRecipe | None = None,
        title: str = "Deliver / Handoff Builder",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 720)
        self._built_in_recipes = built_in_recipes
        self._saved_recipes = list(saved_recipes)
        self._saved_recipe_payload: WorkflowRecipe | None = None
        self._selection_count = selection_count

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        intro = QLabel(
            "Build a repeatable handoff using existing triage operations. Recipes stay human-readable: bundle copy/move/archive or export deliverables with format and sizing options."
        )
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        root_layout.addWidget(intro)

        recipe_group = QGroupBox("Recipe")
        recipe_layout = QGridLayout(recipe_group)
        recipe_layout.setContentsMargins(12, 14, 12, 12)
        recipe_layout.setHorizontalSpacing(10)
        recipe_layout.setVerticalSpacing(8)
        root_layout.addWidget(recipe_group)

        self.recipe_picker = QComboBox()
        self.recipe_picker.currentIndexChanged.connect(self._handle_recipe_selected)
        recipe_layout.addWidget(QLabel("Load"), 0, 0)
        recipe_layout.addWidget(self.recipe_picker, 0, 1, 1, 3)

        self.recipe_name_field = QLineEdit()
        recipe_layout.addWidget(QLabel("Name"), 1, 0)
        recipe_layout.addWidget(self.recipe_name_field, 1, 1, 1, 3)

        self.description_field = QLineEdit()
        recipe_layout.addWidget(QLabel("Description"), 2, 0)
        recipe_layout.addWidget(self.description_field, 2, 1, 1, 3)

        self.save_recipe_button = QPushButton("Save Recipe")
        self.delete_recipe_button = QPushButton("Delete Saved")
        self.save_recipe_button.clicked.connect(self._save_recipe_requested)
        self.delete_recipe_button.clicked.connect(self._delete_recipe_requested)
        recipe_layout.addWidget(self.save_recipe_button, 3, 2)
        recipe_layout.addWidget(self.delete_recipe_button, 3, 3)

        config_group = QGroupBox("Workflow")
        config_form = QFormLayout(config_group)
        config_form.setContentsMargins(12, 14, 12, 12)
        config_form.setSpacing(10)
        root_layout.addWidget(config_group)

        self.content_mode_combo = QComboBox()
        self.content_mode_combo.addItem("Export Deliverables", RECIPE_CONTENT_EXPORT)
        self.content_mode_combo.addItem("Full Bundle", RECIPE_CONTENT_BUNDLE)
        self.content_mode_combo.currentIndexChanged.connect(self._refresh_mode_visibility)
        config_form.addRow("Content", self.content_mode_combo)

        self.transfer_mode_combo = QComboBox()
        self.transfer_mode_combo.addItem("Copy", RECIPE_TRANSFER_COPY)
        self.transfer_mode_combo.addItem("Move", RECIPE_TRANSFER_MOVE)
        self.transfer_mode_combo.addItem("Archive", RECIPE_TRANSFER_ARCHIVE)
        self.transfer_mode_combo.currentIndexChanged.connect(self._update_summary)
        config_form.addRow("Transfer", self.transfer_mode_combo)

        self.destination_root_field = QLineEdit(default_destination_root)
        self.destination_root_button = QPushButton("Browse...")
        self.destination_root_button.clicked.connect(self._choose_destination_root)
        destination_root_row = QWidget()
        destination_root_layout = QHBoxLayout(destination_root_row)
        destination_root_layout.setContentsMargins(0, 0, 0, 0)
        destination_root_layout.setSpacing(8)
        destination_root_layout.addWidget(self.destination_root_field, 1)
        destination_root_layout.addWidget(self.destination_root_button)
        config_form.addRow("Destination Root", destination_root_row)

        self.destination_subfolder_field = QLineEdit()
        self.destination_subfolder_field.textChanged.connect(self._update_summary)
        config_form.addRow("Subfolder", self.destination_subfolder_field)

        self.group_by_record_checkbox = QCheckBox("Create one folder per selected record")
        self.group_by_record_checkbox.toggled.connect(self._update_summary)
        config_form.addRow("Bundle Layout", self.group_by_record_checkbox)

        self.resize_combo = QComboBox()
        self.resize_combo.addItem("Keep Original Size", "")
        for preset in resize_presets():
            self.resize_combo.addItem(preset.name, preset.key)
        self.resize_combo.currentIndexChanged.connect(self._update_summary)
        config_form.addRow("Resize", self.resize_combo)

        self.convert_combo = QComboBox()
        self.convert_combo.addItem("Keep Original Format", "")
        for item in convert_formats():
            self.convert_combo.addItem(item.name, item.suffix)
        self.convert_combo.currentIndexChanged.connect(self._update_summary)
        config_form.addRow("Convert", self.convert_combo)

        self.strip_metadata_checkbox = QCheckBox("Strip metadata from exported files")
        self.strip_metadata_checkbox.toggled.connect(self._update_summary)
        config_form.addRow("Metadata", self.strip_metadata_checkbox)

        rename_row = QWidget()
        rename_layout = QHBoxLayout(rename_row)
        rename_layout.setContentsMargins(0, 0, 0, 0)
        rename_layout.setSpacing(8)
        self.rename_prefix_field = QLineEdit()
        self.rename_prefix_field.setPlaceholderText("Prefix")
        self.rename_suffix_field = QLineEdit()
        self.rename_suffix_field.setPlaceholderText("Suffix")
        self.rename_prefix_field.textChanged.connect(self._update_summary)
        self.rename_suffix_field.textChanged.connect(self._update_summary)
        rename_layout.addWidget(self.rename_prefix_field, 1)
        rename_layout.addWidget(self.rename_suffix_field, 1)
        config_form.addRow("Filename Affixes", rename_row)

        archive_row = QWidget()
        archive_layout = QHBoxLayout(archive_row)
        archive_layout.setContentsMargins(0, 0, 0, 0)
        archive_layout.setSpacing(8)
        self.archive_after_export_checkbox = QCheckBox("Archive exported output")
        self.archive_after_export_checkbox.toggled.connect(self._refresh_mode_visibility)
        self.archive_format_combo = QComboBox()
        self.archive_format_combo.addItem("ZIP", "zip")
        self.archive_format_combo.addItem("7-Zip", "7z")
        self.archive_format_combo.addItem("TAR.GZ", "tar_gz")
        self.archive_format_combo.currentIndexChanged.connect(self._update_summary)
        archive_layout.addWidget(self.archive_after_export_checkbox)
        archive_layout.addWidget(self.archive_format_combo)
        config_form.addRow("Archive", archive_row)

        summary_group = QGroupBox("Preview")
        summary_layout = QVBoxLayout(summary_group)
        summary_layout.setContentsMargins(12, 14, 12, 12)
        root_layout.addWidget(summary_group, 1)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("sectionLabel")
        summary_layout.addWidget(self.summary_label)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMinimumHeight(180)
        summary_layout.addWidget(self.summary_text, 1)

        self.button_box = QDialogButtonBox(self)
        self.run_button = self.button_box.addButton("Run Workflow", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.button_box.accepted.connect(self._accept_if_valid)
        self.button_box.rejected.connect(self.reject)
        root_layout.addWidget(self.button_box)

        self._refresh_recipe_picker()
        if initial_recipe is not None:
            self._apply_recipe(initial_recipe)
        elif built_in_recipes:
            self._apply_recipe(built_in_recipes[0])
        self._refresh_mode_visibility()

    def result_data(self) -> HandoffDialogResult:
        return HandoffDialogResult(
            recipe=self._recipe_from_fields(),
            destination_root=self.destination_root_field.text().strip(),
        )

    def saved_recipes(self) -> tuple[WorkflowRecipe, ...]:
        return tuple(self._saved_recipes)

    def _refresh_recipe_picker(self) -> None:
        self.recipe_picker.blockSignals(True)
        self.recipe_picker.clear()
        self.recipe_picker.addItem("Custom", None)
        if self._built_in_recipes:
            for recipe in self._built_in_recipes:
                self.recipe_picker.addItem(f"Built-in: {recipe.name}", ("builtin", recipe.key))
        if self._saved_recipes:
            for recipe in self._saved_recipes:
                self.recipe_picker.addItem(f"Saved: {recipe.name}", ("saved", recipe.key))
        self.recipe_picker.blockSignals(False)

    def _find_recipe(self, origin: str, recipe_key: str) -> WorkflowRecipe | None:
        recipes = self._built_in_recipes if origin == "builtin" else tuple(self._saved_recipes)
        for recipe in recipes:
            if recipe.key == recipe_key:
                return recipe
        return None

    def _handle_recipe_selected(self) -> None:
        data = self.recipe_picker.currentData()
        if not isinstance(data, tuple) or len(data) != 2:
            self._saved_recipe_payload = None
            return
        origin, recipe_key = data
        if not isinstance(origin, str) or not isinstance(recipe_key, str):
            return
        recipe = self._find_recipe(origin, recipe_key)
        if recipe is not None:
            self._apply_recipe(recipe)
            self._saved_recipe_payload = recipe if origin == "saved" else None

    def _apply_recipe(self, recipe: WorkflowRecipe) -> None:
        self.recipe_name_field.setText(recipe.name)
        self.description_field.setText(recipe.description)
        self._set_combo_value(self.content_mode_combo, recipe.content_mode)
        self._set_combo_value(self.transfer_mode_combo, recipe.transfer_mode)
        self.destination_subfolder_field.setText(recipe.destination_subfolder)
        self.group_by_record_checkbox.setChecked(recipe.group_by_record_folder)
        self._set_combo_value(self.resize_combo, recipe.resize_preset_key)
        self._set_combo_value(self.convert_combo, recipe.convert_suffix)
        self.strip_metadata_checkbox.setChecked(recipe.strip_metadata)
        self.rename_prefix_field.setText(recipe.rename_prefix)
        self.rename_suffix_field.setText(recipe.rename_suffix)
        self.archive_after_export_checkbox.setChecked(recipe.archive_after_export)
        self._set_combo_value(self.archive_format_combo, recipe.archive_format)
        self._update_summary()

    def _recipe_from_fields(self) -> WorkflowRecipe:
        name = " ".join(self.recipe_name_field.text().split()) or "Workflow Recipe"
        return WorkflowRecipe(
            key=recipe_key_for_name(name) or "workflow_recipe",
            name=name,
            description=self.description_field.text().strip(),
            content_mode=str(self.content_mode_combo.currentData() or RECIPE_CONTENT_EXPORT),
            transfer_mode=str(self.transfer_mode_combo.currentData() or RECIPE_TRANSFER_COPY),
            destination_subfolder=self.destination_subfolder_field.text().strip(),
            group_by_record_folder=self.group_by_record_checkbox.isChecked(),
            archive_after_export=self.archive_after_export_checkbox.isChecked(),
            archive_format=str(self.archive_format_combo.currentData() or "zip"),
            resize_preset_key=str(self.resize_combo.currentData() or ""),
            convert_suffix=str(self.convert_combo.currentData() or ""),
            strip_metadata=self.strip_metadata_checkbox.isChecked(),
            rename_prefix=self.rename_prefix_field.text(),
            rename_suffix=self.rename_suffix_field.text(),
        )

    def _save_recipe_requested(self) -> None:
        recipe = self._recipe_from_fields()
        if not recipe.name.strip():
            QMessageBox.warning(self, "Save Recipe", "Enter a recipe name before saving.")
            return
        existing_index = next((index for index, item in enumerate(self._saved_recipes) if item.key == recipe.key), None)
        if existing_index is None:
            existing_index = next((index for index, item in enumerate(self._saved_recipes) if item.name.casefold() == recipe.name.casefold()), None)
        if existing_index is not None:
            self._saved_recipes[existing_index] = recipe
        else:
            self._saved_recipes.append(recipe)
        self._saved_recipe_payload = recipe
        self._refresh_recipe_picker()
        self._select_recipe_in_picker("saved", recipe.key)
        self._update_summary()

    def _delete_recipe_requested(self) -> None:
        recipe = self._saved_recipe_payload
        if recipe is None:
            QMessageBox.information(self, "Delete Recipe", "Select or save a custom recipe first.")
            return
        self._saved_recipes = [item for item in self._saved_recipes if item.key != recipe.key]
        self._saved_recipe_payload = None
        self._refresh_recipe_picker()
        self.recipe_picker.setCurrentIndex(0)
        self._update_summary()

    def _choose_destination_root(self) -> None:
        start = self.destination_root_field.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose Destination Root", start)
        if folder:
            self.destination_root_field.setText(folder)
            self._update_summary()

    def _refresh_mode_visibility(self) -> None:
        export_mode = str(self.content_mode_combo.currentData() or RECIPE_CONTENT_EXPORT) == RECIPE_CONTENT_EXPORT
        self.transfer_mode_combo.setEnabled(not export_mode)
        self.group_by_record_checkbox.setEnabled(not export_mode)
        self.resize_combo.setEnabled(export_mode)
        self.convert_combo.setEnabled(export_mode)
        self.strip_metadata_checkbox.setEnabled(export_mode)
        self.rename_prefix_field.setEnabled(export_mode)
        self.rename_suffix_field.setEnabled(export_mode)
        self.archive_after_export_checkbox.setEnabled(export_mode)
        self.archive_format_combo.setEnabled(export_mode and self.archive_after_export_checkbox.isChecked() or not export_mode)
        self._update_summary()

    def _update_summary(self) -> None:
        recipe = self._recipe_from_fields()
        destination_root = self.destination_root_field.text().strip() or "(choose a destination root)"
        destination_parts = [destination_root]
        if recipe.destination_subfolder:
            destination_parts.append(recipe.destination_subfolder)
        destination_preview = str(Path(*destination_parts)) if destination_parts else destination_root
        lines = [f"{self._selection_count} selected image bundle(s)."]
        lines.extend(recipe_summary_lines(recipe))
        if recipe.uses_transform_export:
            lines.append(f"Exports land in: {destination_preview}")
        elif recipe.transfer_mode == RECIPE_TRANSFER_ARCHIVE:
            lines.append(f"Archive will be created from the selected bundles near: {destination_preview}")
        else:
            lines.append(f"Bundle handoff root: {destination_preview}")
        if recipe.transfer_mode == RECIPE_TRANSFER_MOVE and recipe.content_mode == RECIPE_CONTENT_BUNDLE:
            lines.append("This recipe moves source bundles and is treated as destructive.")
        self.summary_label.setText(recipe.name)
        self.summary_text.setPlainText("\n".join(lines))

    def _accept_if_valid(self) -> None:
        destination_root = self.destination_root_field.text().strip()
        if not destination_root:
            QMessageBox.warning(self, "Destination Root", "Choose a destination root before running the workflow.")
            return
        recipe = self._recipe_from_fields()
        if recipe.content_mode == RECIPE_CONTENT_BUNDLE and recipe.transfer_mode == RECIPE_TRANSFER_MOVE:
            confirmation = QMessageBox.question(
                self,
                "Run Destructive Workflow?",
                "This workflow moves the source bundles into the destination structure.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                return
        self.accept()

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _select_recipe_in_picker(self, origin: str, recipe_key: str) -> None:
        for index in range(self.recipe_picker.count()):
            data = self.recipe_picker.itemData(index)
            if data == (origin, recipe_key):
                self.recipe_picker.setCurrentIndex(index)
                return
