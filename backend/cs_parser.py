"""
cs_parser.py — C# entity parser backed by tree-sitter for a real CST.
Handles: properties, nullability, access modifiers, XML docs, attributes,
namespace detection, and write-back (two-way sync).

Install: pip install tree-sitter tree-sitter-languages
Falls back gracefully to the original regex parser if tree-sitter is absent.
"""

import re
import os
from dataclasses import dataclass, field
from typing import Optional

# ── Tree-sitter bootstrap ─────────────────────────────────────────────────

_TREE_SITTER_OK = False
_ts_parser = None
_CS_LANG = None

try:
    # Prefer the bundled multi-language package (simplest install)
    from tree_sitter_languages import get_language, get_parser as _get_ts_parser
    _CS_LANG = get_language("c_sharp")
    _ts_parser = _get_ts_parser("c_sharp")
    _TREE_SITTER_OK = True
except ImportError:
    try:
        # Fallback: standalone tree-sitter-c-sharp (tree-sitter >= 0.22)
        from tree_sitter import Language, Parser as _TSParser
        import tree_sitter_c_sharp as _tscsharp
        _CS_LANG = Language(_tscsharp.language())
        _ts_parser = _TSParser(_CS_LANG)
        _TREE_SITTER_OK = True
    except Exception as _e:
        print(
            f"[CSForge] tree-sitter not available ({_e}). "
            "Run  pip install tree-sitter tree-sitter-languages  for richer parsing. "
            "Falling back to regex parser."
        )


# ── Data classes (public API — unchanged) ────────────────────────────────

@dataclass
class CSharpProperty:
    name: str
    type: str
    nullable: bool
    required: bool
    access: str = "public"
    has_getter: bool = True
    has_setter: bool = True
    xml_doc: str = ""
    attributes: list = field(default_factory=list)
    line_number: int = 0


@dataclass
class CSharpEntity:
    name: str
    namespace: str
    file_path: str
    properties: list  # List[CSharpProperty]
    base_class: str = ""
    interfaces: list = field(default_factory=list)
    attributes: list = field(default_factory=list)
    xml_doc: str = ""
    is_partial: bool = False
    last_modified: float = 0.0

    def to_dict(self):
        return {
            "name": self.name,
            "namespace": self.namespace,
            "file_path": self.file_path,
            "properties": [
                {
                    "name": p.name,
                    "type": p.type,
                    "nullable": p.nullable,
                    "required": p.required,
                    "access": p.access,
                    "xml_doc": p.xml_doc,
                    "attributes": p.attributes,
                    "line_number": p.line_number,
                }
                for p in self.properties
            ],
            "base_class": self.base_class,
            "interfaces": self.interfaces,
            "attributes": self.attributes,
            "xml_doc": self.xml_doc,
            "is_partial": self.is_partial,
            "last_modified": self.last_modified,
        }


# ── Tree-sitter helpers ───────────────────────────────────────────────────

def _node_text(node, src: bytes) -> str:
    """Return the source text for a tree-sitter node."""
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk_nodes(node, *node_types):
    """Yield all descendant nodes (including self) matching any of the given types."""
    if node.type in node_types:
        yield node
    for child in node.children:
        yield from _walk_nodes(child, *node_types)


def _get_modifiers(node, src: bytes) -> list:
    """Return all modifier texts for a declaration node."""
    return [
        _node_text(c, src)
        for c in node.children
        if c.type == "modifier"
    ]


def _ts_find_namespace(root, src: bytes) -> str:
    """Extract namespace name from file-scoped or block namespace declaration."""
    for ns_node in _walk_nodes(
        root,
        "namespace_declaration",
        "file_scoped_namespace_declaration",
    ):
        name_node = ns_node.child_by_field_name("name")
        if name_node:
            return _node_text(name_node, src).strip()
    return ""


def _ts_find_class(root, src: bytes):
    """Return the first public, non-abstract, non-static class_declaration node."""
    for cls in _walk_nodes(root, "class_declaration"):
        mods = _get_modifiers(cls, src)
        if "public" in mods and "abstract" not in mods and "static" not in mods:
            return cls
    return None


def _ts_class_name(cls_node, src: bytes) -> str:
    name_node = cls_node.child_by_field_name("name")
    return _node_text(name_node, src).strip() if name_node else ""


def _ts_class_bases(cls_node, src: bytes):
    """Return (base_class, interfaces) parsed from the base_list."""
    base_class = ""
    interfaces = []
    bases_node = cls_node.child_by_field_name("bases")
    if not bases_node:
        return base_class, interfaces
    for child in bases_node.children:
        if child.type in ("identifier", "generic_name", "qualified_name"):
            name = _node_text(child, src).strip()
            if name.startswith("I") and len(name) > 1 and name[1].isupper():
                interfaces.append(name)
            else:
                base_class = name
    return base_class, interfaces


def _ts_class_attributes(cls_node, src: bytes) -> list:
    """Return attribute names applied to the class."""
    attrs = []
    for child in cls_node.children:
        if child.type == "attribute_list":
            for attr in _walk_nodes(child, "attribute"):
                name_node = attr.child_by_field_name("name") or (
                    attr.children[0] if attr.children else None
                )
                if name_node:
                    attrs.append(_node_text(name_node, src).strip())
    return attrs


def _ts_xml_doc_above(node, parent_children: list, src: bytes) -> str:
    """
    Collect consecutive /// comment lines immediately before node in its
    parent's child list and extract the summary text.
    """
    idx = next(
        (i for i, c in enumerate(parent_children) if c.id == node.id), None
    )
    if idx is None:
        return ""
    comment_lines = []
    i = idx - 1
    while i >= 0 and parent_children[i].type == "comment":
        text = _node_text(parent_children[i], src).strip()
        if text.startswith("///"):
            comment_lines.insert(0, text.lstrip("/").strip())
        else:
            break
        i -= 1
    if not comment_lines:
        return ""
    full = " ".join(comment_lines)
    m = re.search(r"<summary>(.*?)</summary>", full, re.DOTALL)
    return m.group(1).strip() if m else full.strip()


def _ts_prop_attributes(prop_node, src: bytes) -> list:
    """Return attribute names applied to a property."""
    attrs = []
    for child in prop_node.children:
        if child.type == "attribute_list":
            for attr in _walk_nodes(child, "attribute"):
                name_node = attr.child_by_field_name("name") or (
                    attr.children[0] if attr.children else None
                )
                if name_node:
                    attrs.append(_node_text(name_node, src).strip())
    return attrs


# Type node types that appear as the type field of a property_declaration
_TYPE_NODE_TYPES = frozenset({
    "predefined_type",
    "identifier",
    "nullable_type",
    "generic_name",
    "array_type",
    "qualified_name",
    "tuple_type",
})


def _ts_parse_type(type_node, src: bytes):
    """Return (type_text_without_?, is_nullable) for a type node."""
    if type_node is None:
        return "object", False
    raw = _node_text(type_node, src).strip()
    if type_node.type == "nullable_type":
        # e.g.  string?  →  inner type node + "?"
        inner = type_node.child_by_field_name("type") or type_node.children[0]
        return _node_text(inner, src).strip(), True
    nullable = raw.endswith("?")
    return raw.rstrip("?"), nullable


def _ts_parse_properties(body_node, src: bytes) -> list:
    """Parse all public properties from a declaration_list (class body) node."""
    properties = []
    siblings = body_node.children  # all children of the class body

    for child in siblings:
        if child.type != "property_declaration":
            continue

        mods = _get_modifiers(child, src)
        if "public" not in mods:
            continue
        if "static" in mods or "const" in mods:
            continue

        type_node = child.child_by_field_name("type")
        name_node = child.child_by_field_name("name")
        if not type_node or not name_node:
            continue

        prop_name = _node_text(name_node, src).strip()
        # Skip identifiers that aren't valid C# names (e.g. literals that
        # slipped through, reserved keywords used as names)
        if not re.match(r"^[A-Za-z_]\w*$", prop_name):
            continue

        clean_type, nullable = _ts_parse_type(type_node, src)
        is_required = "required" in mods or (not nullable and clean_type not in ("string", "object"))

        accessors = child.child_by_field_name("accessors") or child.child_by_field_name("accessor_list")
        accessors_text = _node_text(accessors, src) if accessors else ""
        has_getter = "get" in accessors_text
        has_setter = "set" in accessors_text or "init" in accessors_text

        xml_doc = _ts_xml_doc_above(child, siblings, src)
        attrs = _ts_prop_attributes(child, src)
        line_num = child.start_point[0] + 1  # 0-based row → 1-based

        properties.append(CSharpProperty(
            name=prop_name,
            type=clean_type,
            nullable=nullable,
            required=is_required,
            access="public",
            has_getter=has_getter,
            has_setter=has_setter,
            xml_doc=xml_doc,
            attributes=attrs,
            line_number=line_num,
        ))

    return properties


def _ts_find_property_node(root, prop_name: str, src: bytes):
    """Find the first property_declaration node whose name matches prop_name."""
    for node in _walk_nodes(root, "property_declaration"):
        name_node = node.child_by_field_name("name")
        if name_node and _node_text(name_node, src).strip() == prop_name:
            return node
    return None


def _splice(src: bytes, start_byte: int, end_byte: int, replacement: str) -> bytes:
    """Replace bytes[start_byte:end_byte] with replacement (UTF-8 encoded)."""
    return src[:start_byte] + replacement.encode("utf-8") + src[end_byte:]


# ── Public parse API ──────────────────────────────────────────────────────

def parse_cs_file(file_path: str) -> Optional[CSharpEntity]:
    """Parse a C# file and extract the primary public class entity."""
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except Exception:
        return None

    if _TREE_SITTER_OK:
        return _ts_parse_cs_file(file_path, content)
    return _regex_parse_cs_file(file_path, content)


def _ts_parse_cs_file(file_path: str, content: str) -> Optional[CSharpEntity]:
    """tree-sitter backed parse."""
    src = content.encode("utf-8")
    tree = _ts_parser.parse(src)
    root = tree.root_node

    namespace = _ts_find_namespace(root, src)
    cls_node = _ts_find_class(root, src)
    if not cls_node:
        return None

    class_name = _ts_class_name(cls_node, src)
    mods = _get_modifiers(cls_node, src)
    is_partial = "partial" in mods
    base_class, interfaces = _ts_class_bases(cls_node, src)
    class_attrs = _ts_class_attributes(cls_node, src)

    # XML doc for the class itself
    # Look in parent's children (compilation_unit or namespace body)
    parent_children = root.children
    for ns_node in _walk_nodes(root, "namespace_declaration", "file_scoped_namespace_declaration"):
        body = ns_node.child_by_field_name("body")
        if body:
            for c in body.children:
                if c.id == cls_node.id:
                    parent_children = body.children
                    break
    class_xml_doc = _ts_xml_doc_above(cls_node, list(parent_children), src)

    # Class body → properties
    body_node = cls_node.child_by_field_name("body")
    properties = _ts_parse_properties(body_node, src) if body_node else []

    last_mod = os.path.getmtime(file_path) if os.path.exists(file_path) else 0.0

    return CSharpEntity(
        name=class_name,
        namespace=namespace,
        file_path=file_path,
        properties=properties,
        base_class=base_class,
        interfaces=interfaces,
        attributes=class_attrs,
        xml_doc=class_xml_doc,
        is_partial=is_partial,
        last_modified=last_mod,
    )


# ── Regex fallback parser (original logic, preserved intact) ─────────────

def _parse_xml_doc(raw: str) -> str:
    if not raw.strip():
        return ""
    lines = [l.strip().lstrip("///").strip() for l in raw.strip().splitlines()]
    full = " ".join(lines)
    summary = re.search(r"<summary>(.*?)</summary>", full, re.DOTALL)
    if summary:
        return summary.group(1).strip()
    return full.strip()


def _parse_attributes(raw: str) -> list:
    attrs = []
    for match in re.finditer(r"\[([^\]]+)\]", raw):
        content = match.group(1).strip()
        name = re.split(r"[(\s]", content)[0]
        attrs.append(name)
    return attrs


def _extract_block(content: str, start: int) -> str:
    depth = 0
    i = start
    while i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[start + 1:i]
        i += 1
    return content[start:]


def _regex_parse_cs_file(file_path: str, content: str) -> Optional[CSharpEntity]:
    namespace = ""
    ns_match = re.search(r"(?:^|\n)\s*namespace\s+([\w.]+)\s*[{;]", content)
    if ns_match:
        namespace = ns_match.group(1).strip()

    class_pattern = re.compile(
        r"((?:///[^\n]*\n\s*)*)"
        r"((?:\[[\s\S]*?\]\s*\n\s*)*)"
        r"\s*(public)\s+"
        r"((?:partial\s+)?)"
        r"(?!(?:abstract|static|interface|enum|record)\s)"
        r"class\s+"
        r"(\w+)"
        r"(?:\s*:\s*([\w\s,<>?]+?))?"
        r"\s*\{",
        re.MULTILINE,
    )
    match = class_pattern.search(content)
    if not match:
        return None

    xml_comment_raw = match.group(1)
    attributes_raw = match.group(2)
    is_partial = bool(match.group(4).strip())
    class_name = match.group(5)
    inheritance_raw = match.group(6) or ""
    class_xml_doc = _parse_xml_doc(xml_comment_raw)
    class_attr = _parse_attributes(attributes_raw)

    base_class = ""
    interfaces = []
    if inheritance_raw.strip():
        for part in [p.strip() for p in inheritance_raw.split(",")]:
            if not part:
                continue
            if part.startswith("I") and len(part) > 1 and part[1].isupper():
                interfaces.append(part)
            else:
                base_class = part

    class_start = match.end()
    class_body = _extract_block(content, class_start - 1) or content[class_start:]
    properties = _regex_parse_properties(class_body)
    last_mod = os.path.getmtime(file_path) if os.path.exists(file_path) else 0.0

    return CSharpEntity(
        name=class_name,
        namespace=namespace,
        file_path=file_path,
        properties=properties,
        base_class=base_class,
        interfaces=interfaces,
        attributes=class_attr,
        xml_doc=class_xml_doc,
        is_partial=is_partial,
        last_modified=last_mod,
    )


def _regex_parse_properties(class_body: str) -> list:
    properties = []
    prop_pattern = re.compile(
        r"((?:\s*///[^\n]*\n)*)"
        r"((?:\s*\[[\s\S]*?\]\s*\n)*)"
        r"\s*(public|protected|private|internal)\s+"
        r"((?:virtual|override|new|static|readonly)\s+)*"
        r"(required\s+)?"
        r"([\w<>\[\]?,. ]+?)\s+"
        r"(\w+)\s*"
        r"\{[^}]*get[^}]*\}",
        re.MULTILINE,
    )
    for match in prop_pattern.finditer(class_body):
        xml_raw = match.group(1)
        attr_raw = match.group(2)
        access = match.group(3)
        modifiers = match.group(4) or ""
        required_kw = match.group(5) or ""
        type_raw = match.group(6).strip()
        prop_name = match.group(7)

        if prop_name in ("get", "set", "value", "return"):
            continue
        if "static" in modifiers or "const" in modifiers:
            continue

        nullable = type_raw.endswith("?")
        clean_type = type_raw.rstrip("?")
        is_required = bool(required_kw.strip()) or (not nullable and clean_type != "string")
        prop_block = match.group(0)
        has_setter = "set" in prop_block or "init" in prop_block
        line_num = class_body[: match.start()].count("\n") + 1

        properties.append(CSharpProperty(
            name=prop_name,
            type=clean_type,
            nullable=nullable,
            required=is_required,
            access=access,
            has_getter=True,
            has_setter=has_setter,
            xml_doc=_parse_xml_doc(xml_raw),
            attributes=_parse_attributes(attr_raw),
            line_number=line_num,
        ))
    return properties


# ── TWO-WAY SYNC ──────────────────────────────────────────────────────────
# Write-back uses tree-sitter for precise byte-range replacement when
# available, falling back to the original regex strategy.

def _read_file_bytes(file_path: str):
    """Read a .cs file, stripping BOM. Returns raw bytes and decoded str."""
    raw = open(file_path, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw, raw.decode("utf-8")


def rename_property(file_path: str, old_name: str, new_name: str) -> bool:
    """Rename a property in a C# file."""
    try:
        src, _ = _read_file_bytes(file_path)
        if _TREE_SITTER_OK:
            tree = _ts_parser.parse(src)
            prop_node = _ts_find_property_node(tree.root_node, old_name, src)
            if prop_node is None:
                return False
            name_node = prop_node.child_by_field_name("name")
            if name_node is None:
                return False
            new_src = _splice(src, name_node.start_byte, name_node.end_byte, new_name)
            open(file_path, "wb").write(new_src)
            return True
        else:
            content = open(file_path, "r", encoding="utf-8-sig").read()
            pattern = re.compile(
                r"(public\s+[\w<>\[\]?,. ]+\s+)" + re.escape(old_name) + r"(\s*\{)"
            )
            new_content = pattern.sub(r"\g<1>" + new_name + r"\2", content)
            if new_content == content:
                return False
            open(file_path, "w", encoding="utf-8").write(new_content)
            return True
    except Exception:
        return False


def change_property_type(file_path: str, prop_name: str, new_type: str, nullable: bool) -> bool:
    """Change the type of a property in a C# file."""
    try:
        src, _ = _read_file_bytes(file_path)
        type_with_null = new_type + ("?" if nullable else "")
        if _TREE_SITTER_OK:
            tree = _ts_parser.parse(src)
            prop_node = _ts_find_property_node(tree.root_node, prop_name, src)
            if prop_node is None:
                return False
            type_node = prop_node.child_by_field_name("type")
            if type_node is None:
                return False
            new_src = _splice(src, type_node.start_byte, type_node.end_byte, type_with_null)
            open(file_path, "wb").write(new_src)
            return True
        else:
            content = open(file_path, "r", encoding="utf-8-sig").read()
            pattern = re.compile(
                r"(public\s+)[\w<>\[\]?,. ]+?(\s+" + re.escape(prop_name) + r"\s*\{)"
            )
            new_content = pattern.sub(r"\g<1>" + type_with_null + r"\2", content)
            if new_content == content:
                return False
            open(file_path, "w", encoding="utf-8").write(new_content)
            return True
    except Exception:
        return False


def toggle_nullable(file_path: str, prop_name: str, nullable: bool) -> bool:
    """Toggle nullability of a property."""
    try:
        src, _ = _read_file_bytes(file_path)
        if _TREE_SITTER_OK:
            tree = _ts_parser.parse(src)
            prop_node = _ts_find_property_node(tree.root_node, prop_name, src)
            if prop_node is None:
                return False
            type_node = prop_node.child_by_field_name("type")
            if type_node is None:
                return False

            if nullable:
                # Only add ? if not already nullable
                if type_node.type == "nullable_type":
                    return True  # already nullable, no-op
                current_type = _node_text(type_node, src).strip()
                new_src = _splice(
                    src, type_node.start_byte, type_node.end_byte,
                    current_type + "?"
                )
            else:
                # Strip the ? — if it's a nullable_type node, replace with inner type
                if type_node.type == "nullable_type":
                    inner = type_node.child_by_field_name("type") or type_node.children[0]
                    inner_text = _node_text(inner, src).strip()
                    new_src = _splice(
                        src, type_node.start_byte, type_node.end_byte, inner_text
                    )
                else:
                    current = _node_text(type_node, src).strip()
                    if not current.endswith("?"):
                        return True  # already non-nullable
                    new_src = _splice(
                        src, type_node.start_byte, type_node.end_byte,
                        current.rstrip("?")
                    )

            open(file_path, "wb").write(new_src)
            return True
        else:
            content = open(file_path, "r", encoding="utf-8-sig").read()
            if nullable:
                pattern = re.compile(
                    r"(public\s+)([\w<>\[\]. ]+?)(\s+" + re.escape(prop_name) + r"\s*\{)"
                )
                def add_null(m):
                    return m.group(1) + m.group(2).rstrip("?") + "?" + m.group(3)
                new_content = pattern.sub(add_null, content)
            else:
                pattern = re.compile(
                    r"(public\s+)([\w<>\[\]. ]+?)\?(\s+" + re.escape(prop_name) + r"\s*\{)"
                )
                new_content = pattern.sub(r"\g<1>\g<2>\g<3>", content)
            if new_content == content:
                return False
            open(file_path, "w", encoding="utf-8").write(new_content)
            return True
    except Exception:
        return False


def add_property(file_path: str, prop_name: str, prop_type: str, nullable: bool) -> bool:
    """Add a new property to a C# class."""
    try:
        src, content = _read_file_bytes(file_path)
        null_suffix = "?" if nullable else ""
        new_prop = f"        public {prop_type}{null_suffix} {prop_name} {{ get; set; }}\n"

        if _TREE_SITTER_OK:
            tree = _ts_parser.parse(src)
            cls_node = _ts_find_class(tree.root_node, src)
            if cls_node is None:
                return False
            body_node = cls_node.child_by_field_name("body")
            if body_node is None:
                return False

            # Find the last property_declaration in the class body
            last_prop = None
            for child in body_node.children:
                if child.type == "property_declaration":
                    last_prop = child

            if last_prop:
                # Insert after the last char of the property's line
                insert_byte = last_prop.end_byte
                # Advance past the rest of the current line (newline char)
                while insert_byte < len(src) and src[insert_byte:insert_byte+1] not in (b"\n", b""):
                    insert_byte += 1
                if insert_byte < len(src):
                    insert_byte += 1  # include the \n
                new_src = src[:insert_byte] + new_prop.encode("utf-8") + src[insert_byte:]
            else:
                # No properties yet — insert before closing } of body
                close_byte = body_node.end_byte - 1
                new_src = src[:close_byte] + new_prop.encode("utf-8") + src[close_byte:]

            open(file_path, "wb").write(new_src)
            return True
        else:
            content = open(file_path, "r", encoding="utf-8-sig").read()
            last_prop_matches = list(re.finditer(
                r"( {4,8}public\s+[\w<>\[\]?,. ]+\s+\w+\s*\{[^}]*\}[^\n]*\n)",
                content,
            ))
            if last_prop_matches:
                insert_pos = last_prop_matches[-1].end()
                new_content = content[:insert_pos] + new_prop + content[insert_pos:]
            else:
                closing = content.rfind("    }")
                if closing == -1:
                    closing = content.rfind("}")
                new_content = content[:closing] + new_prop + content[closing:]
            open(file_path, "w", encoding="utf-8").write(new_content)
            return True
    except Exception:
        return False


def remove_property(file_path: str, prop_name: str) -> bool:
    """Remove a property (and its preceding XML doc / attributes) from a C# class."""
    try:
        src, content = _read_file_bytes(file_path)

        if _TREE_SITTER_OK:
            tree = _ts_parser.parse(src)
            prop_node = _ts_find_property_node(tree.root_node, prop_name, src)
            if prop_node is None:
                return False

            # Walk backwards through parent's children to include preceding
            # comment and attribute_list nodes as part of the deletion range.
            parent = prop_node.parent
            siblings = parent.children if parent else []
            idx = next((i for i, c in enumerate(siblings) if c.id == prop_node.id), None)

            start_byte = prop_node.start_byte
            if idx is not None:
                i = idx - 1
                while i >= 0 and siblings[i].type in ("comment", "attribute_list"):
                    start_byte = siblings[i].start_byte
                    i -= 1

            end_byte = prop_node.end_byte
            # Consume trailing newline so we don't leave a blank line
            if end_byte < len(src) and src[end_byte:end_byte+1] == b"\n":
                end_byte += 1

            # Also consume leading whitespace on the first deleted line
            # so indentation isn't orphaned
            while start_byte > 0 and src[start_byte-1:start_byte] in (b" ", b"\t"):
                start_byte -= 1
            if start_byte > 0 and src[start_byte-1:start_byte] == b"\n":
                start_byte -= 1
                if start_byte > 0 and src[start_byte-1:start_byte] == b"\r":
                    start_byte -= 1
                start_byte += 1  # keep the preceding newline, remove from it onward

            new_src = src[:start_byte] + src[end_byte:]
            open(file_path, "wb").write(new_src)
            return True
        else:
            content = open(file_path, "r", encoding="utf-8-sig").read()
            pattern = re.compile(
                r"([ \t]*(?:///[^\n]*\n[ \t]*)*"
                r"(?:\[[\s\S]*?\]\s*\n[ \t]*)*)?"
                r"[ \t]*public\s+[\w<>\[\]?,. ]+\s+"
                + re.escape(prop_name)
                + r"\s*\{[^}]*\}[^\n]*\n",
                re.MULTILINE,
            )
            new_content = pattern.sub("", content, count=1)
            if new_content == content:
                return False
            open(file_path, "w", encoding="utf-8").write(new_content)
            return True
    except Exception:
        return False


# ── Directory scan ────────────────────────────────────────────────────────

def scan_directory(directory: str) -> list:
    """Scan a directory recursively for C# entity files."""
    entities = []
    if not os.path.isdir(directory):
        return entities

    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in {
            "bin", "obj", "node_modules", ".git", ".vs",
            "Migrations", "migrations", "wwwroot",
        }]
        for fname in files:
            if not fname.endswith(".cs"):
                continue
            fpath = os.path.join(root, fname)
            entity = parse_cs_file(fpath)
            if entity and entity.properties:
                entities.append(entity)

    return entities
