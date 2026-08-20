from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode


DATE_TOKEN_MAP = (
    ("YYYY", "%Y"),
    ("YY", "%y"),
    ("MM", "%m"),
    ("DD", "%d"),
    ("HH", "%H"),
    ("mm", "%M"),
    ("ss", "%S"),
)

IMAGE_MIME_PREFIX = "image/"
FRONTMATTER_VALUE_TOKEN = "$v"
LEGACY_FRONTMATTER_VALUE_TOKEN = "%prop%"
MAX_FILENAME_BYTES = 240
EMBED_MIME_PREFIXES = ("image/", "audio/", "video/")
EMBED_FILE_TYPES = {"Image", "Audio", "Video"}
WIKILINK_TEMPLATE = f"[[{FRONTMATTER_VALUE_TOKEN}]]"
LEGACY_WIKILINK_TEMPLATE = f"[[{LEGACY_FRONTMATTER_VALUE_TOKEN}]]"
DEFAULT_ATTACHMENT_LINK_STYLE = "relative"
COVER_IMAGE_MAPPING_NAME = "cover_image"


@dataclass
class RelationInfo:
    key: str
    name: str
    relation_format: int | None
    max_count: int | None


@dataclass
class FileInfo:
    object_id: str
    name: str
    source: str | None
    mime: str | None


class StyledString(str):
    def __new__(cls, value: str, quote_style: str | None = None) -> StyledString:
        instance = super().__new__(cls, value)
        instance.quote_style = quote_style
        return instance


def normalize_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_")


def sanitize_filename(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"[\\/:*?\"<>|]", "-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(". ")
    return cleaned or "Untitled"


def shorten_filename(name: str, max_bytes: int = MAX_FILENAME_BYTES) -> str:
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name

    path = Path(name)
    suffix = path.suffix
    stem = path.stem or "Untitled"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    reserved = len(f"-{digest}{suffix}".encode("utf-8"))
    allowed_stem_bytes = max(1, max_bytes - reserved)

    truncated_stem = stem
    while len(truncated_stem.encode("utf-8")) > allowed_stem_bytes and truncated_stem:
        truncated_stem = truncated_stem[:-1]
    truncated_stem = truncated_stem.rstrip(" .") or "Untitled"

    shortened = f"{truncated_stem}-{digest}{suffix}"
    if len(shortened.encode("utf-8")) <= max_bytes:
        return shortened

    fallback_stem_bytes = max(1, max_bytes - len(f"-{digest}".encode("utf-8")))
    fallback_stem = "f"
    while len(fallback_stem.encode("utf-8")) > fallback_stem_bytes:
        fallback_stem = fallback_stem[:-1]
    return f"{fallback_stem}-{digest}"


def translate_date_format(value: str) -> str:
    translated = value
    for token, replacement in DATE_TOKEN_MAP:
        translated = translated.replace(token, replacement)
    return translated


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: Path) -> dict[str, Any]:
    root = yaml.compose(path.read_text(encoding="utf-8"))
    if root is None:
        return {}
    loaded = _node_to_config_value(root, preserve_style=False)
    if not isinstance(loaded, dict):
        raise ValueError("Config root must be a YAML mapping.")
    return loaded


def _node_to_config_value(node: Node, preserve_style: bool = True) -> Any:
    if isinstance(node, MappingNode):
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            result[str(_node_to_config_value(key_node, preserve_style=False))] = _node_to_config_value(value_node)
        return result

    if isinstance(node, SequenceNode):
        return [_node_to_config_value(item) for item in node.value]

    if isinstance(node, ScalarNode):
        if node.tag == "tag:yaml.org,2002:null":
            return None
        if node.tag == "tag:yaml.org,2002:bool":
            return node.value.lower() in {"true", "yes", "on"}
        if node.tag == "tag:yaml.org,2002:int":
            return int(node.value)
        if node.tag == "tag:yaml.org,2002:float":
            return float(node.value)

        value = node.value
        if preserve_style:
            quote_style = node.style if node.style in {"'", '"'} else None
            return StyledString(value, quote_style)
        return value

    raise TypeError(f"Unsupported YAML node: {type(node).__name__}")


class AnytypeIndex:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root
        self.relations_by_key: dict[str, RelationInfo] = {}
        self.relation_keys_by_name: dict[str, str] = {}
        self.option_name_by_id: dict[str, str] = {}
        self.type_name_by_id: dict[str, str] = {}
        self.object_name_by_id: dict[str, str] = {}
        self.bookmark_url_by_object_id: dict[str, str] = {}
        self.collection_names_by_member_id: dict[str, list[str]] = defaultdict(list)
        self.file_info_by_object_id: dict[str, FileInfo] = {}
        self._load_types()
        self._load_relations()
        self._load_relation_options()
        self._load_object_names()
        self._load_files()

    def _iter_json_files(self, folder_name: str) -> list[Path]:
        folder = self.source_root / folder_name
        if not folder.exists():
            return []
        return sorted(folder.glob("*.json"))

    def _load_types(self) -> None:
        for path in self._iter_json_files("types"):
            data = load_json(path)
            details = data.get("snapshot", {}).get("data", {}).get("details", {})
            object_id = details.get("id")
            name = details.get("name")
            if object_id and name:
                self.type_name_by_id[object_id] = name.strip()
                self.object_name_by_id[object_id] = name.strip()

    def _load_relations(self) -> None:
        for path in self._iter_json_files("relations"):
            data = load_json(path)
            details = data.get("snapshot", {}).get("data", {}).get("details", {})
            relation_key = details.get("relationKey")
            name = details.get("name")
            if not relation_key or not name:
                continue
            info = RelationInfo(
                key=relation_key,
                name=name.strip(),
                relation_format=details.get("relationFormat"),
                max_count=details.get("relationMaxCount"),
            )
            self.relations_by_key[relation_key] = info
            self.relation_keys_by_name[name.strip()] = relation_key
            object_id = details.get("id")
            if object_id:
                self.object_name_by_id[object_id] = name.strip()

    def _load_relation_options(self) -> None:
        for path in self._iter_json_files("relationsOptions"):
            data = load_json(path)
            details = data.get("snapshot", {}).get("data", {}).get("details", {})
            object_id = details.get("id")
            name = details.get("name")
            if object_id and name:
                self.option_name_by_id[object_id] = name.strip()
                self.object_name_by_id[object_id] = name.strip()

    def _load_object_names(self) -> None:
        for path in self._iter_json_files("objects"):
            data = load_json(path)
            snapshot = data.get("snapshot", {}).get("data", {})
            details = snapshot.get("details", {})
            object_id = details.get("id")
            name = details.get("name")
            if object_id and name:
                stripped_name = name.strip()
                self.object_name_by_id[object_id] = stripped_name
                if "ot-bookmark" in (snapshot.get("objectTypes") or []):
                    source_url = str(details.get("source") or "").strip()
                    if source_url:
                        self.bookmark_url_by_object_id[object_id] = source_url
                if "ot-collection" in (snapshot.get("objectTypes") or []):
                    if self._is_generated_import_collection(details):
                        continue
                    members = ((snapshot.get("collections") or {}).get("objects") or [])
                    for member_id in members:
                        if member_id and stripped_name not in self.collection_names_by_member_id[member_id]:
                            self.collection_names_by_member_id[member_id].append(stripped_name)

    def _is_generated_import_collection(self, details: dict[str, Any]) -> bool:
        name = str(details.get("name") or "").strip()
        flags = details.get("internalFlags") or []
        return name.startswith("Protobuf Import ") and 3 in flags

    def _load_files(self) -> None:
        for path in self._iter_json_files("filesObjects"):
            data = load_json(path)
            details = data.get("snapshot", {}).get("data", {}).get("details", {})
            object_id = details.get("id")
            name = details.get("name")
            if not object_id:
                continue
            self.file_info_by_object_id[object_id] = FileInfo(
                object_id=object_id,
                name=(name or object_id).strip(),
                source=details.get("source"),
                mime=details.get("fileMimeType"),
            )
            if name:
                self.object_name_by_id[object_id] = name.strip()


class Converter:
    DATE_FIELDS = {"createdDate", "lastModifiedDate"}

    def __init__(
        self,
        source_root: Path,
        output_root: Path,
        config: dict[str, Any],
        match: str | None = None,
    ) -> None:
        self.source_root = source_root
        self.output_root = output_root
        self.config = config
        self.match = match.lower() if match else None
        self.date_format = translate_date_format(config.get("date_format", "YYYY-MM-DD"))
        self.attachments_folder = config.get("attachments_folder", "attachments")
        self.attachment_link_style = self._normalize_attachment_link_style(config.get("attachment_link_style"))
        self.exclude_types = set(config.get("exclude_types", []) or [])
        self.collection_mappings = list(config.get("collections", []) or [])
        self.cover_image_mappings = list(config.get("cover_image", []) or [])
        self.index = AnytypeIndex(source_root)
        self.copied_attachments: dict[str, Path] = {}
        self.used_note_names: dict[str, int] = defaultdict(int)

    def convert(self) -> int:
        self.output_root.mkdir(parents=True, exist_ok=True)
        converted = 0
        for path in sorted((self.source_root / "objects").glob("*.json")):
            data = load_json(path)
            if data.get("sbType") != "Page":
                continue
            details = data.get("snapshot", {}).get("data", {}).get("details", {})
            if self._should_exclude(details):
                continue
            title = (details.get("name") or "").strip()
            if self.match and self.match not in title.lower():
                continue
            output_path = self._note_output_path(details)
            markdown = self._render_note(data, output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
            self._apply_file_timestamps(details, output_path)
            converted += 1
        return converted

    def _should_exclude(self, details: dict[str, Any]) -> bool:
        type_id = details.get("type")
        type_name = self.index.type_name_by_id.get(type_id, "")
        return type_name in self.exclude_types

    def _note_output_path(self, details: dict[str, Any]) -> Path:
        title = sanitize_filename((details.get("name") or "").strip())
        base_name = title or details.get("id", "Untitled")
        counter = self.used_note_names[base_name]
        self.used_note_names[base_name] += 1
        if counter:
            base_name = f"{base_name} {counter + 1}"
        safe_filename = shorten_filename(f"{base_name}.md")
        return self.output_root / safe_filename

    def _render_note(self, data: dict[str, Any], note_path: Path) -> str:
        snapshot = data.get("snapshot", {}).get("data", {})
        details = snapshot.get("details", {})
        blocks = snapshot.get("blocks", [])
        frontmatter = self._build_frontmatter(details, note_path)
        body_lines = self._render_body(blocks, note_path)
        body = "\n".join(self._collapse_blank_lines(body_lines)).strip()
        if frontmatter:
            yaml_text = self._dump_frontmatter(frontmatter)
            if body:
                return f"---\n{yaml_text}\n---\n\n{body}\n"
            return f"---\n{yaml_text}\n---\n"
        if body:
            return f"{body}\n"
        return ""

    def _build_frontmatter(self, details: dict[str, Any], note_path: Path) -> dict[str, Any]:
        mappings = self.config.get("properties", {}) or {}
        frontmatter: dict[str, Any] = {}
        for source_name, targets in mappings.items():
            raw_value, relation = self._resolve_source_value(details, source_name, note_path)
            if raw_value in (None, "", []):
                continue
            resolved_value = self._resolve_property_value(raw_value, source_name, relation)
            if resolved_value in (None, "", []):
                continue
            for target in targets or []:
                for destination_key, template in (target or {}).items():
                    rendered_value = self._apply_template(resolved_value, template, source_name=source_name)
                    self._merge_frontmatter_value(frontmatter, destination_key, rendered_value)
        self._apply_collection_mappings(frontmatter, details)
        self._apply_cover_image_mappings(frontmatter, details, note_path)
        return frontmatter

    def _apply_collection_mappings(self, frontmatter: dict[str, Any], details: dict[str, Any]) -> None:
        if not self.collection_mappings:
            return

        object_id = str(details.get("id") or "").strip()
        if not object_id:
            return

        collection_names = self.index.collection_names_by_member_id.get(object_id, [])
        if not collection_names:
            return

        for target in self.collection_mappings:
            for destination_key, template in (target or {}).items():
                rendered_value = self._apply_template(collection_names, template)
                self._merge_frontmatter_value(frontmatter, destination_key, rendered_value)

    def _apply_cover_image_mappings(
        self,
        frontmatter: dict[str, Any],
        details: dict[str, Any],
        note_path: Path,
    ) -> None:
        if not self.cover_image_mappings:
            return

        cover_path = self._resolve_cover_image_path(details, note_path)
        if not cover_path:
            return

        for target in self.cover_image_mappings:
            for destination_key, template in (target or {}).items():
                rendered_value = self._apply_template(cover_path, template, source_name=COVER_IMAGE_MAPPING_NAME)
                self._merge_frontmatter_value(frontmatter, destination_key, rendered_value)

    def _apply_file_timestamps(self, details: dict[str, Any], output_path: Path) -> None:
        created_timestamp = self._coerce_timestamp(details.get("createdDate"))
        modified_timestamp = self._coerce_timestamp(details.get("lastModifiedDate"))
        effective_timestamp = modified_timestamp or created_timestamp

        if effective_timestamp is not None:
            os.utime(output_path, (effective_timestamp, effective_timestamp))

        if created_timestamp is not None:
            self._set_macos_file_date(output_path, created_timestamp, flag="-d")
        if modified_timestamp is not None:
            self._set_macos_file_date(output_path, modified_timestamp, flag="-m")

    def _coerce_timestamp(self, value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _set_macos_file_date(self, output_path: Path, timestamp: float, flag: str) -> None:
        formatted = datetime.fromtimestamp(timestamp).strftime("%m/%d/%Y %H:%M:%S")
        subprocess.run(
            ["/usr/bin/SetFile", flag, formatted, str(output_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    def _resolve_source_value(
        self,
        details: dict[str, Any],
        source_name: str,
        note_path: Path | None = None,
    ) -> tuple[Any, RelationInfo | None]:
        candidate = source_name
        actual_key = None
        relation = None

        if candidate in details:
            actual_key = candidate
        elif candidate in self.index.relation_keys_by_name:
            relation_key = self.index.relation_keys_by_name[candidate]
            if relation_key in details:
                actual_key = relation_key
                relation = self.index.relations_by_key.get(relation_key)

        if actual_key is None:
            return None, None

        if relation is None:
            relation = self.index.relations_by_key.get(actual_key)

        return details.get(actual_key), relation

    def _resolve_cover_image_path(self, details: dict[str, Any], note_path: Path | None) -> str | None:
        if note_path is None:
            return None

        cover_id = str(details.get("coverId") or "").strip()
        if not cover_id:
            return None

        note_title = (details.get("name") or "cover").strip() or "cover"
        attachment_path = self._copy_attachment(cover_id, f"{sanitize_filename(note_title)}-cover")
        if attachment_path is None:
            return None
        return self._attachment_reference(attachment_path)

    def _resolve_property_value(
        self,
        value: Any,
        source_name: str,
        relation: RelationInfo | None,
    ) -> Any:
        if isinstance(value, list):
            resolved_items = [self._resolve_scalar_value(item, source_name, relation) for item in value]
            filtered = [item for item in resolved_items if item not in (None, "")]
            if not filtered:
                return None
            if relation and relation.max_count == 1 and len(filtered) == 1:
                return filtered[0]
            return filtered
        return self._resolve_scalar_value(value, source_name, relation)

    def _resolve_scalar_value(
        self,
        value: Any,
        source_name: str,
        relation: RelationInfo | None,
    ) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if self._looks_like_date(source_name, relation):
                return self._format_timestamp(value)
            return value
        if not isinstance(value, str):
            return str(value)

        if value in self.index.option_name_by_id:
            return self.index.option_name_by_id[value]
        if value in self.index.type_name_by_id:
            return self.index.type_name_by_id[value]
        if value in self.index.object_name_by_id:
            return self.index.object_name_by_id[value]
        return value.strip()

    def _looks_like_date(self, source_name: str, relation: RelationInfo | None) -> bool:
        if source_name in self.DATE_FIELDS:
            return True
        if relation and relation.relation_format == 4:
            return True
        return False

    def _format_timestamp(self, value: int | float) -> str:
        timestamp = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return timestamp.astimezone().strftime(self.date_format)

    def _apply_template(self, value: Any, template: Any, source_name: str | None = None) -> Any:
        template_text = str(template)
        quote_style = self._quote_style_for(template)

        if self._is_wikilink_template(template_text):
            if source_name == COVER_IMAGE_MAPPING_NAME and not isinstance(value, list):
                return StyledString(self._wikilink(str(value)), quote_style)
            if isinstance(value, list):
                return [StyledString(self._wikilink(str(item)), quote_style) for item in value]
            return [StyledString(self._wikilink(str(value)), quote_style)]
        if isinstance(value, list):
            return [self._render_template_string(template_text, item, quote_style) for item in value]
        return self._render_template_string(template_text, value, quote_style)

    def _merge_frontmatter_value(self, frontmatter: dict[str, Any], key: str, value: Any) -> None:
        if key not in frontmatter:
            frontmatter[key] = value
            return

        existing = frontmatter[key]
        if isinstance(value, list):
            if isinstance(existing, list):
                frontmatter[key] = existing + value
            else:
                frontmatter[key] = [existing] + value
            return

        # Scalar mappings always replace the previously rendered value.
        # This keeps config order intuitive: later scalar mappings override earlier ones.
        frontmatter[key] = value

    def _dump_frontmatter(self, frontmatter: dict[str, Any]) -> str:
        lines: list[str] = []
        for key, value in frontmatter.items():
            lines.extend(self._render_yaml_mapping_entry(str(key), value, indent_level=0))
        return "\n".join(lines)

    def _render_yaml_mapping_entry(self, key: str, value: Any, indent_level: int) -> list[str]:
        indent = "  " * indent_level
        rendered_key = self._render_yaml_key(key)

        if isinstance(value, dict):
            lines = [f"{indent}{rendered_key}:"]
            for child_key, child_value in value.items():
                lines.extend(self._render_yaml_mapping_entry(str(child_key), child_value, indent_level + 1))
            return lines

        if isinstance(value, list):
            if not value:
                return [f"{indent}{rendered_key}: []"]
            lines = [f"{indent}{rendered_key}:"]
            for item in value:
                lines.extend(self._render_yaml_list_item(item, indent_level + 1))
            return lines

        return [f"{indent}{rendered_key}: {self._render_yaml_scalar(value)}"]

    def _render_yaml_list_item(self, value: Any, indent_level: int) -> list[str]:
        indent = "  " * indent_level

        if isinstance(value, dict):
            items = list(value.items())
            if not items:
                return [f"{indent}- {{}}"]
            first_key, first_value = items[0]
            lines = [f"{indent}- {self._render_yaml_key(str(first_key))}: {self._render_yaml_inline(first_value)}"]
            for child_key, child_value in items[1:]:
                lines.extend(self._render_yaml_mapping_entry(str(child_key), child_value, indent_level + 1))
            return lines

        if isinstance(value, list):
            if not value:
                return [f"{indent}- []"]
            lines = [f"{indent}-"]
            for item in value:
                lines.extend(self._render_yaml_list_item(item, indent_level + 1))
            return lines

        return [f"{indent}- {self._render_yaml_scalar(value)}"]

    def _render_yaml_inline(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return self._render_yaml_scalar(str(value))
        return self._render_yaml_scalar(value)

    def _render_yaml_key(self, key: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9 _-]+", key):
            return key
        return self._render_yaml_quoted_string(key)

    def _render_yaml_scalar(self, value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, StyledString):
            return self._render_yaml_styled_string(value)
        return self._render_yaml_quoted_string(str(value))

    def _render_yaml_quoted_string(self, value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'

    def _render_yaml_single_quoted_string(self, value: str) -> str:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"

    def _render_yaml_styled_string(self, value: StyledString) -> str:
        quote_style = self._quote_style_for(value)
        rendered_value = str(value)
        if quote_style == "'":
            return self._render_yaml_single_quoted_string(rendered_value)
        if quote_style == '"':
            return self._render_yaml_quoted_string(rendered_value)
        if self._is_plain_yaml_scalar_safe(rendered_value):
            return rendered_value
        return self._render_yaml_quoted_string(rendered_value)

    def _quote_style_for(self, value: Any) -> str | None:
        return getattr(value, "quote_style", None)

    def _contains_value_token(self, template_text: str) -> bool:
        return FRONTMATTER_VALUE_TOKEN in template_text or LEGACY_FRONTMATTER_VALUE_TOKEN in template_text

    def _replace_value_token(self, template_text: str, replacement: str) -> str:
        updated = template_text.replace(FRONTMATTER_VALUE_TOKEN, replacement)
        return updated.replace(LEGACY_FRONTMATTER_VALUE_TOKEN, replacement)

    def _is_wikilink_template(self, template_text: str) -> bool:
        return template_text in {WIKILINK_TEMPLATE, LEGACY_WIKILINK_TEMPLATE}

    def _render_template_string(self, template_text: str, value: Any, quote_style: str | None) -> StyledString:
        if self._contains_value_token(template_text):
            rendered = self._replace_value_token(template_text, str(value))
        else:
            rendered = template_text
        return StyledString(rendered, quote_style)

    def _is_plain_yaml_scalar_safe(self, value: str) -> bool:
        if value == "" or value != value.strip():
            return False
        if value.lower() in {"null", "true", "false", "~"}:
            return False
        if value[0] in "-?:,[]{}#&*!|>'\"%@`":
            return False
        if value[-1] in ":#":
            return False
        if ": " in value or " #" in value:
            return False
        if any(char in value for char in "\n\r\t"):
            return False
        return True

    def _render_body(self, blocks: list[dict[str, Any]], note_path: Path) -> list[str]:
        if not blocks:
            return []

        block_by_id = {block.get("id"): block for block in blocks if block.get("id")}
        root = blocks[0]
        lines: list[str] = []
        for child_id in root.get("childrenIds", []):
            if child_id == "header":
                continue
            lines.extend(self._render_block(child_id, block_by_id, note_path, indent_level=0))
        return lines

    def _render_block(
        self,
        block_id: str,
        block_by_id: dict[str, dict[str, Any]],
        note_path: Path,
        indent_level: int,
    ) -> list[str]:
        block = block_by_id.get(block_id)
        if not block:
            return []

        if "text" in block:
            return self._render_text_block(block, block_by_id, note_path, indent_level)
        if "link" in block:
            return self._render_link_block(block, indent_level)
        if "file" in block:
            return self._render_file_block(block, note_path)
        if "bookmark" in block:
            bookmark = block.get("bookmark", {})
            url = bookmark.get("url") or bookmark.get("targetObjectId")
            title = (bookmark.get("title") or bookmark.get("name") or url or "Link").strip()
            if not url:
                return []
            target_id = bookmark.get("targetObjectId")
            if target_id:
                linked_target = self._render_link_target(target_id, title)
                if linked_target:
                    return [linked_target, ""]
            return [f"[{title}]({url})", ""]
        if "latex" in block:
            latex = block.get("latex", {}) or {}
            text = (latex.get("text") or "").strip()
            if not text:
                return []
            if latex.get("processor") == "Youtube":
                return [f"[{text}]({text})", ""]
            return ["$$", text, "$$", ""]

        child_lines: list[str] = []
        for child_id in block.get("childrenIds", []):
            child_lines.extend(self._render_block(child_id, block_by_id, note_path, indent_level))
        return child_lines

    def _render_text_block(
        self,
        block: dict[str, Any],
        block_by_id: dict[str, dict[str, Any]],
        note_path: Path,
        indent_level: int,
    ) -> list[str]:
        text_data = block.get("text", {})
        raw_text = text_data.get("text") or ""
        text = self._render_inline_text(raw_text, ((text_data.get("marks") or {}).get("marks") or [])).strip()
        style = (text_data.get("style") or "Paragraph").lower()
        children = block.get("childrenIds", [])
        lines: list[str] = []
        indent = "\t" * indent_level

        if style == "title":
            pass
        elif "heading" in style:
            level = self._heading_level(style)
            if text:
                lines.append(f"{'#' * level} {text}")
                lines.append("")
        elif "quote" in style:
            if text:
                lines.append(f"> {text}")
                lines.append("")
        elif "checkbox" in style or "todo" in style:
            checked = bool(text_data.get("checked"))
            marker = "x" if checked else " "
            lines.append(f"{indent}- [{marker}] {text}".rstrip())
        elif any(token in style for token in ("bullet", "marked", "list")):
            lines.append(f"{indent}- {text}".rstrip())
        elif "number" in style:
            lines.append(f"{indent}1. {text}".rstrip())
        elif "code" in style:
            lines.extend(["```", text, "```", ""])
        else:
            if text:
                lines.append(text)
                lines.append("")

        child_indent = indent_level
        if any(token in style for token in ("checkbox", "todo", "bullet", "marked", "list", "number")):
            child_indent += 1

        for child_id in children:
            lines.extend(self._render_block(child_id, block_by_id, note_path, child_indent))
        return lines

    def _render_link_block(self, block: dict[str, Any], indent_level: int) -> list[str]:
        link = block.get("link", {}) or {}
        target_id = link.get("targetBlockId")
        if not target_id:
            return []

        line = self._render_link_target(target_id)
        if line:
            if indent_level > 0:
                return [f"{'\t' * indent_level}- {line}"]
            return [line, ""]
        return []

    def _render_inline_text(self, text: str, marks: list[dict[str, Any]]) -> str:
        if not text:
            return ""
        if not marks:
            return text

        normalized_marks: list[tuple[int, int, dict[str, Any]]] = []
        text_length = len(text)
        for mark in marks:
            range_data = mark.get("range") or {}
            start = max(0, min(text_length, int(range_data.get("from", 0))))
            end = max(0, min(text_length, int(range_data.get("to", 0))))
            if end < start:
                start, end = end, start
            if start == end:
                if mark.get("type") in {"Link", "Mention", "Object"} and text_length > 0:
                    end = text_length
                else:
                    continue
            normalized_marks.append((start, end, mark))

        if not normalized_marks:
            return text

        boundaries = {0, text_length}
        for start, end, _ in normalized_marks:
            boundaries.add(start)
            boundaries.add(end)

        pieces: list[str] = []
        ordered_boundaries = sorted(boundaries)
        for start, end in zip(ordered_boundaries, ordered_boundaries[1:]):
            if start >= end:
                continue
            segment = text[start:end]
            active = [mark for mark_start, mark_end, mark in normalized_marks if mark_start <= start and end <= mark_end]
            pieces.append(self._apply_inline_marks(segment, active))
        return "".join(pieces)

    def _apply_inline_marks(self, segment: str, marks: list[dict[str, Any]]) -> str:
        result = segment

        mention_mark = next((mark for mark in marks if mark.get("type") in {"Mention", "Object"}), None)
        if mention_mark:
            target_id = mention_mark.get("param")
            target_name = self.index.object_name_by_id.get(target_id)
            display_name = segment.strip()
            bookmark_url = self.index.bookmark_url_by_object_id.get(str(target_id or ""))
            if bookmark_url:
                label = display_name or target_name or bookmark_url
                result = f"[{label}]({bookmark_url})"
            elif target_name:
                if display_name and display_name != target_name:
                    result = f"[[{target_name}|{display_name}]]"
                else:
                    result = self._wikilink(target_name)

        link_mark = next((mark for mark in marks if mark.get("type") == "Link"), None)
        if link_mark and mention_mark is None:
            url = str(link_mark.get("param") or "").strip()
            label = segment.strip() or url
            if url:
                result = f"[{label}]({url})"

        if any(mark.get("type") == "Keyboard" for mark in marks):
            result = f"`{result}`"
        if any(mark.get("type") == "Bold" for mark in marks):
            result = f"**{result}**"
        if any(mark.get("type") == "Italic" for mark in marks):
            result = f"*{result}*"
        if any(mark.get("type") == "Underscored" for mark in marks):
            result = f"<u>{result}</u>"

        return result

    def _wikilink(self, target_name: str) -> str:
        return f"[[{target_name.strip()}]]"

    def _render_link_target(self, target_id: str, fallback_label: str | None = None) -> str | None:
        bookmark_url = self.index.bookmark_url_by_object_id.get(target_id)
        target_name = self.index.object_name_by_id.get(target_id)
        if bookmark_url:
            label = (fallback_label or target_name or bookmark_url).strip()
            return f"[{label}]({bookmark_url})"
        if target_name:
            return self._wikilink(target_name)
        return None

    def _render_file_block(self, block: dict[str, Any], note_path: Path) -> list[str]:
        file_data = block.get("file", {})
        target_object_id = file_data.get("targetObjectId")
        name = (file_data.get("name") or "attachment").strip()
        if not target_object_id:
            return [f"[{name}]()", ""]

        attachment_path = self._copy_attachment(target_object_id, name)
        if attachment_path is None:
            return [f"[{name}]()", ""]

        link_target = self._attachment_reference(attachment_path)
        if self._is_embedded_media(file_data):
            return [f"![[{link_target}]]", ""]
        return [f"[{name}]({link_target})", ""]

    def _is_embedded_media(self, file_data: dict[str, Any]) -> bool:
        mime = str(file_data.get("mime") or "").strip().lower()
        file_type = str(file_data.get("type") or "").strip()
        style = str(file_data.get("style") or "").strip()
        return (
            any(mime.startswith(prefix) for prefix in EMBED_MIME_PREFIXES)
            or file_type in EMBED_FILE_TYPES
            or style == "Embed"
        )

    def _copy_attachment(self, target_object_id: str, fallback_name: str) -> Path | None:
        metadata = self.index.file_info_by_object_id.get(target_object_id)
        if not metadata or not metadata.source:
            return None

        source_path = self.source_root / metadata.source
        if not source_path.exists():
            return None

        cache_key = str(source_path)
        if cache_key in self.copied_attachments:
            return self.copied_attachments[cache_key]

        destination_dir = self.output_root / self.attachments_folder
        destination_dir.mkdir(parents=True, exist_ok=True)

        destination_name = sanitize_filename(source_path.name or metadata.name or fallback_name)
        destination_name = shorten_filename(destination_name)
        destination_path = destination_dir / destination_name
        stem = destination_path.stem
        suffix = destination_path.suffix
        counter = 2
        while destination_path.exists() and not source_path.samefile(destination_path):
            destination_path = destination_dir / f"{stem} {counter}{suffix}"
            counter += 1

        shutil.copy2(source_path, destination_path)
        self.copied_attachments[cache_key] = destination_path
        return destination_path

    def _attachment_reference(self, attachment_path: Path) -> str:
        if self.attachment_link_style == "filename only":
            return attachment_path.name
        return attachment_path.relative_to(self.output_root).as_posix()

    def _normalize_attachment_link_style(self, value: Any) -> str:
        normalized = str(value or DEFAULT_ATTACHMENT_LINK_STYLE).strip().lower()
        if normalized in {"filename only", "filename_only", "name only", "name_only"}:
            return "filename only"
        return DEFAULT_ATTACHMENT_LINK_STYLE

    def _heading_level(self, style: str) -> int:
        match = re.search(r"(\d+)$", style)
        if not match:
            return 2
        return max(1, min(6, int(match.group(1))))

    def _collapse_blank_lines(self, lines: list[str]) -> list[str]:
        collapsed: list[str] = []
        previous_blank = False
        for line in lines:
            blank = line.strip() == ""
            if blank and previous_blank:
                continue
            collapsed.append(line)
            previous_blank = blank
        while collapsed and collapsed[-1].strip() == "":
            collapsed.pop()
        return collapsed


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Convert an Anytype Anyblock export to Obsidian markdown.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=script_dir.parent,
        help="Root folder of the Anytype export.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=script_dir / "output",
        help="Directory where markdown files will be written.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=script_dir / "config.yaml",
        help="Path to the converter config YAML file.",
    )
    parser.add_argument(
        "--match",
        type=str,
        default=None,
        help="Only convert notes whose title contains this string.",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    converter = Converter(
        source_root=args.source_root.resolve(),
        output_root=args.output_root.resolve(),
        config=config,
        match=args.match,
    )
    converted = converter.convert()
    print(f"Converted {converted} note(s) into {args.output_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
