import asyncio
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("custom_guides_test_package")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__ + ".custom_guides", ROOT / "custom_guides.py")
guides = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guides
spec.loader.exec_module(guides)
prompt_spec = importlib.util.spec_from_file_location(package.__name__ + ".prompt_guides", ROOT / "prompt_guides.py")
prompts = importlib.util.module_from_spec(prompt_spec)
prompt_spec.loader.exec_module(prompts)


class CustomGuideTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = patch.object(guides, "GUIDE_DIR", self.root / "prompt_guides")
        self.directory.start()
        self.addCleanup(self.directory.stop)

    def test_import_restart_dynamic_options_and_folder_rename_keep_workflow_value(self):
        result = guides.add_guide("My guide", "guide.md", b"Use a fixed camera.", new_folder="Video")
        value = result["value"]
        folder = result["folders"][0]["id"]
        self.assertEqual(guides.load_guide(value), ("My guide", "Use a fixed camera."))
        self.assertIn(value, prompts.get_guide_options())
        self.assertIn("Use a fixed camera.", prompts.combine_system_prompt("Keep instructions", value))
        guides.rename_folder(folder, "My renamed folder")
        self.assertEqual(guides.list_guides()["folders"][0]["name"], "My renamed folder")
        self.assertEqual(guides.load_guide(value)[0], "My guide")
        self.assertEqual(json.loads((guides.GUIDE_DIR / "custom/index.json").read_text())["guides"][0]["value"], value)
        second = guides.add_guide("Another", "guide.txt", b"Keep framing.", folder=folder)
        self.assertEqual(second["guides"][-1]["folder"], folder)

    def test_custom_display_name_does_not_enable_builtin_prompt_behavior(self):
        result = guides.add_guide("Fibo 2", "a.txt", b"Use my custom guide.")
        combined = prompts.combine_system_prompt("Preserve details.", result["value"])
        self.assertIn("Selected prompt writing guide: Fibo 2", combined)
        self.assertNotIn("T2I with Fibo 2 Dev", combined)
        self.assertNotIn("Rephrase descriptive wording", combined)

    def test_rename_guide_preserves_value_text_and_persisted_workflow(self):
        created = guides.add_guide("Original", "a.md", b"Keep this guidance.", new_folder="Video")
        value = created["value"]
        result = guides.rename_guide(value, "  New guide name  ")
        self.assertEqual(result["guides"][0], {"value": value, "name": "New guide name", "folder": created["folders"][0]["id"]})
        self.assertEqual(guides.load_guide(value), ("New guide name", "Keep this guidance."))
        self.assertIn(value, prompts.get_guide_options())
        self.assertIn("Selected prompt writing guide: New guide name", prompts.combine_system_prompt("", value))
        self.assertEqual(guides._catalog()["guides"], result["guides"])

    def test_rename_rejects_duplicate_within_folder_without_affecting_other_folders(self):
        first = guides.add_guide("First", "a.md", b"first")
        second = guides.add_guide("Second", "a.md", b"second")
        with self.assertRaisesRegex(ValueError, "already exists"):
            guides.rename_guide(second["value"], "FIRST")
        self.assertEqual(guides.load_guide(second["value"])[0], "Second")
        third = guides.add_guide("Third", "a.md", b"third", new_folder="Video")
        guides.rename_guide(third["value"], "First")
        self.assertEqual(guides.load_guide(first["value"])[1], "first")
        self.assertEqual(guides.load_guide(third["value"])[0], "First")

    def test_guide_and_folder_mutations_reject_builtins_unknown_ids_and_path_forgery(self):
        created = guides.add_guide("Safe", "a.md", b"safe")
        before = guides.list_guides()
        for value in ("MiniMax H3 - Pika", "Fibo 2", "None", "Custom:../../outside", "Custom:" + "f" * 32, None, [created["value"]]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    guides.rename_guide(value, "Renamed")
                with self.assertRaises(ValueError):
                    guides.delete_guide(value)
        for folder in ("MiniMax H3", "Seedance", "../../outside", "f" * 32, None, []):
            with self.assertRaises(ValueError):
                guides.delete_folder(folder)
        self.assertEqual(guides.list_guides(), before)
        self.assertEqual(guides.load_guide(created["value"]), ("Safe", "safe"))

    def test_delete_folder_moves_guides_to_root_preserving_names_values_and_contents(self):
        root_guide = guides.add_guide("Same name", "a.md", b"root rules")
        child = guides.add_guide("Same name", "b.md", b"folder rules", new_folder="Video")
        folder = child["folders"][0]["id"]
        another = guides.add_guide("Another guide", "c.md", b"other rules", folder=folder)
        result = guides.delete_folder(folder)
        self.assertEqual(result["folders"], [])
        self.assertTrue(all(guide["folder"] == "" for guide in result["guides"]))
        self.assertEqual([guide["name"] for guide in result["guides"]], ["Same name", "Same name", "Another guide"])
        for value, text in ((root_guide["value"], "root rules"), (child["value"], "folder rules"), (another["value"], "other rules")):
            self.assertEqual(guides.load_guide(value)[1], text)
            self.assertIn(value, prompts.get_guide_options())
        self.assertEqual(guides._catalog()["guides"], result["guides"])

    def test_delete_guide_removes_text_catalog_and_options(self):
        created = guides.add_guide("Remove me", "a.md", b"rules")
        value = created["value"]
        path = guides.GUIDE_DIR / "custom" / (value[7:] + ".md")
        result = guides.delete_guide(value)
        self.assertEqual(result["guides"], [])
        self.assertFalse(path.exists())
        self.assertNotIn(value, prompts.get_guide_options())
        with self.assertRaisesRegex(ValueError, "not found"):
            guides.load_guide(value)
        with self.assertRaises(ValueError):
            guides.delete_guide(value)

    def test_failed_catalog_write_preserves_guide_text_and_folder_membership(self):
        created = guides.add_guide("Keep me", "a.md", b"rules", new_folder="Video")
        before = guides.list_guides()
        operations = ((guides.rename_guide, (created["value"], "New name")), (guides.delete_guide, (created["value"],)), (guides.delete_folder, (created["folders"][0]["id"],)))
        for function, args in operations:
            with self.subTest(operation=function.__name__), patch.object(guides, "_save_catalog", side_effect=PermissionError("private path")):
                with self.assertRaises(PermissionError):
                    function(*args)
            self.assertEqual(guides.list_guides(), before)
            self.assertEqual(guides.load_guide(created["value"]), ("Keep me", "rules"))

    def test_failed_text_deletion_restores_catalog_and_preserves_contents(self):
        created = guides.add_guide("Keep me", "a.md", b"rules")
        value = created["value"]
        path = guides.GUIDE_DIR / "custom" / (value[7:] + ".md")
        original_unlink = Path.unlink
        def deny_text_deletion(target, *args, **kwargs):
            if target == path:
                raise PermissionError("private path")
            return original_unlink(target, *args, **kwargs)
        with patch.object(Path, "unlink", deny_text_deletion):
            with self.assertRaises(PermissionError):
                guides.delete_guide(value)
        self.assertEqual(guides.load_guide(value), ("Keep me", "rules"))
        self.assertIn(value, prompts.get_guide_options())

    def test_delete_guide_rejects_escaping_symlink_before_mutating_catalog(self):
        created = guides.add_guide("Keep me", "a.md", b"rules")
        value = created["value"]
        path = guides.GUIDE_DIR / "custom" / (value[7:] + ".md")
        outside = self.root / "outside.txt"
        outside.write_text("outside rules", encoding="utf-8")
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symlinks is unavailable on this account")
        before = guides.list_guides()
        with self.assertRaisesRegex(ValueError, "inside prompt_guides"):
            guides.delete_guide(value)
        self.assertEqual(guides.list_guides(), before)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside rules")

    def test_duplicate_names_and_bundled_folders_cannot_be_changed(self):
        result = guides.add_guide("Guide", "a.md", b"rules", new_folder="My folder")
        folder = result["folders"][0]["id"]
        for args in (("guide", "a.txt", b"other", folder), ("Other", "a.txt", b"other", "", "my FOLDER")):
            with self.assertRaisesRegex(ValueError, "already exists"):
                guides.add_guide(*args)
        for name in ("MiniMax H3", "Seedance"):
            with self.assertRaisesRegex(ValueError, "bundled folders"):
                guides.add_guide("Other", "a.md", b"rules", new_folder=name)
            with self.assertRaisesRegex(ValueError, "Bundled folders"):
                guides.rename_folder(name, "Changed")
        with self.assertRaisesRegex(ValueError, "Unknown custom folder"):
            guides.add_guide("Other", "a.md", b"rules", folder="../outside")

    def test_generated_paths_ignore_filenames_and_names_and_reject_invalid_ids(self):
        result = guides.add_guide("../../guide", "../../outside.md", b"rules", new_folder="../folder")
        self.assertTrue((guides.GUIDE_DIR / "custom" / (result["value"][7:] + ".md")).is_file())
        self.assertFalse((self.root / "outside.md").exists())
        for value in ("Custom:../../outside", "Custom:" + "f" * 32, "Custom:", None):
            with self.assertRaises(ValueError):
                guides.load_guide(value)

    def test_symlink_storage_escape_is_rejected(self):
        guides.GUIDE_DIR.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (guides.GUIDE_DIR / "custom").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is unavailable on this account")
        with self.assertRaisesRegex(ValueError, "inside prompt_guides"):
            guides.add_guide("Guide", "a.txt", b"rules")

    def test_plain_text_encoding_empty_binary_and_limits(self):
        self.assertEqual(guides.extract_text("guide.TEXT", "A café".encode("utf-16")), "A café")
        self.assertEqual(guides.extract_text("a.md", b"\xef\xbb\xbfRules"), "Rules")
        for filename, data in (("a.md", b"   "), ("a.txt", b"a\x00b"), ("a.txt", b"\xff"), ("a.exe", b"rules"), ("a.doc", b"rules")):
            with self.assertRaises(ValueError):
                guides.extract_text(filename, data)
        with patch.object(guides, "MAX_UPLOAD", 4):
            with self.assertRaisesRegex(ValueError, "8 MB"):
                guides.extract_text("a.md", b"large")

    def test_docx_extracts_paragraphs_tables_and_unicode_without_xml(self):
        data = BytesIO()
        with ZipFile(data, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello</w:t><w:tab/><w:t>world</w:t><w:br/><w:t>café</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table rule</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        self.assertEqual(guides.extract_text("a.docx", data.getvalue()), "Hello\tworld\ncafé\nTable rule")
        with self.assertRaisesRegex(ValueError, "Could not read the DOCX"):
            guides.extract_text("a.docx", b"invalid zip")

    @unittest.skipIf(guides.pymupdf is None, "PyMuPDF is unavailable")
    def test_pdf_extracts_text_and_rejects_scanned_and_invalid_documents(self):
        with guides.pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), "Keep the subject's identity.")
            data = document.tobytes()
        self.assertIn("Keep the subject", guides.extract_text("a.pdf", data))
        with guides.pymupdf.open() as document:
            document.new_page()
            data = document.tobytes()
        with self.assertRaisesRegex(ValueError, "no readable text"):
            guides.extract_text("a.pdf", data)
        with self.assertRaisesRegex(ValueError, "Could not read the PDF"):
            guides.extract_text("a.pdf", b"broken")

    def test_missing_pdf_reader_has_actionable_text_fallback(self):
        with patch.object(guides, "pymupdf", None):
            with self.assertRaisesRegex(ValueError, "Export the PDF as TXT"):
                guides.extract_text("a.pdf", b"pdf")

    def test_routes_register_local_endpoints(self):
        calls = []
        class Routes:
            def __getattr__(self, method):
                return lambda path: lambda function: calls.append((method, path, function))
        guides.register_routes(Routes())
        self.assertEqual([(method, path) for method, path, _ in calls], [("get", "/goat/prompt-guides"), ("post", "/goat/prompt-guides"), ("patch", "/goat/prompt-guides"), ("delete", "/goat/prompt-guides"), ("patch", "/goat/prompt-guides/folders"), ("delete", "/goat/prompt-guides/folders")])
        self.assertTrue(all(hasattr(function, "__wrapped__") for _, _, function in calls))
        response = asyncio.run(guides.get_guides(None))
        self.assertEqual(json.loads(response.text)["guides"], [])
        async def request_json():
            return {"id": "MiniMax H3", "name": "Renamed"}
        response = asyncio.run(guides.update_folder(SimpleNamespace(json=request_json)))
        self.assertEqual(response.status, 400)


class CustomGuideRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_guide_management_routes_require_guard_and_persist_mutations(self):
        with TemporaryDirectory() as directory, patch.object(guides, "GUIDE_DIR", Path(directory) / "prompt_guides"):
            created = guides.add_guide("Original", "a.md", b"keep this text", new_folder="Video")
            value = created["value"]
            folder = created["folders"][0]["id"]
            app = web.Application()
            routes = web.RouteTableDef()
            guides.register_routes(routes)
            app.add_routes(routes)
            async with TestClient(TestServer(app)) as client:
                for method, path, body in (("PATCH", "/goat/prompt-guides", {"value": value, "name": "Changed"}), ("DELETE", "/goat/prompt-guides", {"value": value}), ("DELETE", "/goat/prompt-guides/folders", {"id": folder})):
                    response = await client.request(method, path, json=body)
                    self.assertEqual(response.status, 403)
                self.assertEqual(guides.load_guide(value), ("Original", "keep this text"))
                headers = {"X-GOAT-Request": "1"}
                response = await client.patch("/goat/prompt-guides", json={"value": value, "name": "Changed"}, headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                result = await response.json()
                self.assertEqual(result["guides"][0]["value"], value)
                self.assertEqual(result["guides"][0]["name"], "Changed")
                self.assertIn("formats", result)
                response = await client.delete("/goat/prompt-guides/folders", json={"id": folder}, headers=headers)
                self.assertEqual(response.status, 200)
                result = await response.json()
                self.assertEqual(result["folders"], [])
                self.assertEqual(result["guides"][0]["folder"], "")
                self.assertEqual(guides.load_guide(value), ("Changed", "keep this text"))
                response = await client.delete("/goat/prompt-guides", json={"value": value}, headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["guides"], [])
                for method, path, body in (("PATCH", "/goat/prompt-guides", {"value": "MiniMax H3 - Pika", "name": "Changed"}), ("DELETE", "/goat/prompt-guides", {"value": "MiniMax H3 - Pika"}), ("DELETE", "/goat/prompt-guides/folders", {"id": "MiniMax H3"})):
                    response = await client.request(method, path, json=body, headers=headers)
                    self.assertEqual(response.status, 400)
                    self.assertIn("error", await response.json())

    async def test_multipart_import_rename_catalog_and_reject_invalid_file(self):
        with TemporaryDirectory() as directory, patch.object(guides, "GUIDE_DIR", Path(directory) / "prompt_guides"):
            app = web.Application()
            routes = web.RouteTableDef()
            guides.register_routes(routes)
            app.add_routes(routes)
            async with TestClient(TestServer(app), headers={"X-GOAT-Request": "1"}) as client:
                form = FormData()
                form.add_field("name", "Actual upload")
                form.add_field("new_folder", "Uploaded guides")
                form.add_field("file", b"Keep the scene.", filename="a.md")
                response = await client.post("/goat/prompt-guides", data=form)
                self.assertEqual(response.status, 200)
                created = await response.json()
                folder = created["folders"][0]["id"]
                response = await client.patch("/goat/prompt-guides/folders", json={"id": folder, "name": "Renamed"})
                self.assertEqual(response.status, 200)
                response = await client.get("/goat/prompt-guides")
                catalog = await response.json()
                self.assertEqual(catalog["guides"][0]["value"], created["value"])
                self.assertEqual(catalog["folders"][0]["name"], "Renamed")
                form = FormData()
                form.add_field("name", "Invalid")
                form.add_field("file", b"bad", filename="a.exe")
                response = await client.post("/goat/prompt-guides", data=form)
                self.assertEqual(response.status, 400)
                self.assertIn("Unsupported file type", (await response.json())["error"])
                response = await client.post("/goat/prompt-guides", json={"name": "Invalid"})
                self.assertEqual(response.status, 400)
                self.assertIn("multipart upload", (await response.json())["error"])
                response = await client.post("/goat/prompt-guides", data=b"broken", headers={"Content-Type": "multipart/form-data"})
                self.assertEqual(response.status, 400)
                self.assertIn("error", await response.json())

    async def test_storage_route_errors_are_actionable_and_hide_private_paths(self):
        async def request_json():
            return {"id": "folder", "name": "New name"}
        with patch.object(guides, "list_guides", side_effect=PermissionError("C:/private/secret")):
            response = await guides.get_guides(None)
            self.assertEqual(response.status, 500)
            self.assertIn("Check access", response.text)
            self.assertNotIn("private", response.text)
        with patch.object(guides, "rename_folder", side_effect=PermissionError("C:/private/secret")):
            response = await guides.update_folder(SimpleNamespace(json=request_json))
            self.assertEqual(response.status, 500)
            self.assertIn("Check write access", response.text)
            self.assertNotIn("private", response.text)
        for service, handler in (("rename_guide", guides.update_guide), ("delete_guide", guides.remove_guide), ("delete_folder", guides.remove_folder)):
            with self.subTest(service=service), patch.object(guides, service, side_effect=PermissionError("C:/private/secret")):
                response = await handler(SimpleNamespace(json=request_json))
                self.assertEqual(response.status, 500)
                self.assertIn("Check write access", response.text)
                self.assertNotIn("private", response.text)


if __name__ == "__main__":
    unittest.main()
