# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung
import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from aiohttp import web

try:
    import pymupdf
except ImportError:
    pymupdf = None


GUIDE_DIR = Path(__file__).parent / "prompt_guides"
MAX_UPLOAD = 8 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024
FORMATS = (".md", ".txt", ".text", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv", ".docx", ".pdf")
_ID = re.compile(r"[0-9a-f]{32}")
_LOCK = RLock()


def _path(filename):
    base = GUIDE_DIR.resolve()
    root = GUIDE_DIR / "custom"
    path = root / filename
    if not root.resolve().is_relative_to(base) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Custom prompt-guide storage must stay inside prompt_guides/custom.")
    return path


def _catalog():
    path = _path("index.json")
    if not path.exists():
        return {"guides": [], "folders": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_catalog(catalog):
    path = _path("index.json")
    temporary = _path(f"{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def list_guides():
    with _LOCK:
        return {**_catalog(), "formats": list(FORMATS)}


def _name(value, kind):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120 or any(ord(char) < 32 for char in value):
        raise ValueError(f"Enter a {kind} name of 1–120 characters without control characters.")
    return value.strip()


def _folder_name(name, folders, exclude=""):
    name = _name(name, "folder")
    if name.casefold() in {"minimax h3", "seedance"}:
        raise ValueError("Choose a custom folder name; bundled folders cannot be changed.")
    if any(folder["id"] != exclude and folder["name"].casefold() == name.casefold() for folder in folders):
        raise ValueError("A custom folder with that name already exists.")
    return name


def _text(text):
    if len(text) > MAX_TEXT:
        raise ValueError("Extracted guide text exceeds 2 million characters.")
    if not text.strip():
        raise ValueError("The file contains no readable text. Export scanned documents as text first.")
    if "\x00" in text:
        raise ValueError("The file contains binary data. Choose a text document.")
    return text


def extract_text(filename, data):
    if len(data) > MAX_UPLOAD:
        raise ValueError("Prompt-guide files must be 8 MB or smaller.")
    extension = Path(filename).suffix.lower()
    if extension not in FORMATS:
        raise ValueError("Unsupported file type. Use MD, TXT, DOCX, PDF, or another listed text format; export legacy DOC as DOCX.")
    if extension == ".docx":
        try:
            with ZipFile(BytesIO(data)) as document:
                if len(document.infolist()) > 4096:
                    raise ValueError("The Word document contains too many archive entries.")
                info = document.getinfo("word/document.xml")
                if info.file_size > MAX_UPLOAD * 2:
                    raise ValueError("The Word document text is too large.")
                with document.open(info) as member:
                    xml = member.read(MAX_UPLOAD * 2 + 1)
                if len(xml) > MAX_UPLOAD * 2:
                    raise ValueError("The Word document text is too large.")
                root = ElementTree.fromstring(xml)
        except (BadZipFile, KeyError, ElementTree.ParseError, RuntimeError) as exc:
            raise ValueError("Could not read the DOCX document. Export it as text or a valid DOCX file.") from exc
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for paragraph in root.iter(namespace + "p"):
            paragraphs.append("".join(element.text or "" if element.tag == namespace + "t" else "\t" if element.tag == namespace + "tab" else "\n" if element.tag == namespace + "br" else "" for element in paragraph.iter()))
        return _text("\n".join(paragraphs))
    if extension == ".pdf":
        if pymupdf is None:
            raise ValueError("PDF import requires PyMuPDF in ComfyUI's Python environment. Export the PDF as TXT or DOCX instead.")
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("Unlock the PDF before importing it.")
                if len(document) > 200:
                    raise ValueError("PDF guides must contain 200 pages or fewer.")
                pages = []
                length = 0
                for page in document:
                    text = page.get_text()
                    length += len(text)
                    if length > MAX_TEXT:
                        raise ValueError("Extracted guide text exceeds 2 million characters.")
                    pages.append(text)
        except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
            raise ValueError("Could not read the PDF. Export it as text first.") from exc
        return _text("\n".join(pages))
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        return _text(data.decode(encoding))
    except UnicodeError as exc:
        raise ValueError("Save the text file as UTF-8 or UTF-16 before importing it.") from exc


def add_guide(name, filename, data, folder="", new_folder=""):
    name = _name(name, "guide")
    text = extract_text(filename, data)
    if not isinstance(folder, str) or not isinstance(new_folder, str) or folder and new_folder.strip():
        raise ValueError("Choose an existing folder or enter a new folder name.")
    with _LOCK:
        catalog = _catalog()
        if new_folder.strip():
            folder_name = _folder_name(new_folder, catalog["folders"])
            folder = uuid4().hex
            catalog["folders"].append({"id": folder, "name": folder_name})
        elif folder and not any(item["id"] == folder for item in catalog["folders"]):
            raise ValueError("Unknown custom folder. Choose a folder from the list.")
        if any(item["folder"] == folder and item["name"].casefold() == name.casefold() for item in catalog["guides"]):
            raise ValueError("A guide with that name already exists in this folder.")
        guide_id = uuid4().hex
        value = "Custom:" + guide_id
        path = _path(guide_id + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            output.write(text)
        catalog["guides"].append({"value": value, "name": name, "folder": folder})
        try:
            _save_catalog(catalog)
        except OSError:
            path.unlink()
            raise
        return {**catalog, "formats": list(FORMATS), "value": value}


def rename_folder(folder_id, name):
    with _LOCK:
        catalog = _catalog()
        folder = next((item for item in catalog["folders"] if item["id"] == folder_id), None)
        if folder is None:
            raise ValueError("Unknown custom folder. Bundled folders cannot be renamed.")
        folder["name"] = _folder_name(name, catalog["folders"], exclude=folder_id)
        _save_catalog(catalog)
        return {**catalog, "formats": list(FORMATS)}


def load_guide(value):
    if not isinstance(value, str) or not value.startswith("Custom:") or not _ID.fullmatch(value[7:]):
        raise ValueError("Unknown custom prompt guide. Choose an item from the dropdown.")
    with _LOCK:
        guide = next((item for item in _catalog()["guides"] if item["value"] == value), None)
        if guide is None:
            raise ValueError("Custom prompt guide was not found. Import it on this ComfyUI installation first.")
        path = _path(value[7:] + ".md")
        if not path.is_file():
            raise ValueError("Custom prompt-guide text is missing. Restore it in prompt_guides/custom.")
        return guide["name"], _text(path.read_text(encoding="utf-8"))


async def get_guides(request):
    try:
        return web.json_response(await asyncio.to_thread(list_guides))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError:
        return web.json_response({"error": "Could not read custom guides. Check access to prompt_guides/custom."}, status=500)


async def import_guide(request):
    try:
        if request.content_type != "multipart/form-data":
            raise ValueError("Choose a file and submit it as a multipart upload.")
        reader = await request.multipart()
        values = {}
        filename = ""
        data = bytearray()
        total = 0
        async for part in reader:
            if part.name not in {"name", "folder", "new_folder", "file"} or part.name in values:
                raise ValueError("Invalid or duplicate upload field.")
            content = bytearray()
            while chunk := await part.read_chunk():
                total += len(chunk)
                if total > MAX_UPLOAD + 4096:
                    raise ValueError("Prompt-guide files must be 8 MB or smaller.")
                content.extend(chunk)
            if part.name == "file":
                filename = part.filename or ""
                data = content
                values["file"] = True
            else:
                if len(content) > 1024:
                    raise ValueError("Guide and folder names are too long.")
                values[part.name] = content.decode("utf-8")
        result = await asyncio.to_thread(add_guide, values.get("name", ""), filename, data, values.get("folder", ""), values.get("new_folder", ""))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except (web.HTTPBadRequest, AssertionError):
        return web.json_response({"error": "Malformed file upload. Choose the file again."}, status=400)
    except OSError:
        return web.json_response({"error": "Could not save the guide. Check write access and free space in prompt_guides/custom."}, status=500)
    return web.json_response(result)


async def update_folder(request):
    try:
        values = await request.json()
        if not isinstance(values, dict):
            raise ValueError("Supply a custom folder id and name.")
        result = await asyncio.to_thread(rename_folder, values.get("id"), values.get("name"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError:
        return web.json_response({"error": "Could not rename the folder. Check write access to prompt_guides/custom."}, status=500)
    return web.json_response(result)


def register_routes(routes):
    routes.get("/goat/prompt-guides")(get_guides)
    routes.post("/goat/prompt-guides")(import_guide)
    routes.patch("/goat/prompt-guides/folders")(update_folder)
