import json
import re
import subprocess
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
# The page and the scripts it runs Python from.
PAGE_SCRIPTS = ("index.html", "workers/host.js", "workers/python_worker.js", "workers/audio_worklet.js")


def _local_asset_paths_from_html(source):
    """Extract local (non-http) href/src paths referenced by an HTML shell."""
    paths = set()
    for match in re.finditer(r'''(?:href|src)=["']([^"']+)["']''', source):
        ref = match.group(1)
        if ref.startswith(("http://", "https://", "#")):
            continue
        paths.add(ref.split("?")[0].split("#")[0])
    return paths


def _sw_shell(sw_source):
    """The files sw.js caches at install: its SHELL array."""
    match = re.search(r"const SHELL = \[(.*?)\];", sw_source, re.S)
    if not match:
        return []
    return [m.group(1) for m in re.finditer(r"""['"](\./[^'"]*)['"]""", match.group(1))]


def _page_imports(html):
    """Local modules the page's script imports, and the worker it registers."""
    refs = set(re.findall(r"""import (?:[^;"']*? from )?["'](\./[^"']+)["']""", html))
    refs |= set(re.findall(r"""register\(["'](\./[^"']+)["']\)""", html))
    return refs


def _worker_imports(source):
    """Files a worker script loads: static imports and new URL(..., import.meta.url),
    outside comments; folders (a base URL) aren't files."""
    source = re.sub(r"^\s*//.*$", "", source, flags=re.M)
    refs = set(re.findall(r"""import (?:[^;"']*? from )?["']([^"']+)["']""", source))
    refs |= set(re.findall(r"""new URL\(["']([^"']+)["'], import\.meta\.url\)""", source))
    return {ref for ref in refs if not ref.endswith("/")}


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


class TemplateTests(unittest.TestCase):
    def test_manifest_is_valid_json(self):
        manifest = json.loads(_read("manifest.json"))
        self.assertEqual(manifest["start_url"], "./index.html")
        for icon in manifest["icons"]:
            self.assertTrue((ROOT / icon["src"]).is_file(), icon["src"])

    def test_worker_files_exist(self):
        """Every Python file app/boot_config.js hands a worker is in the tree
        (vendored ones aside). lib/ is studio's lib/, two folders up, as
        tools/serve.py serves it: the song model Ostinato shares with Vamp."""
        cfg = json.loads(_read("app/boot_config.js").split("export default", 1)[1].rstrip().rstrip(";"))
        for worker in ("ui", "engine"):
            for src in cfg[worker]["files"]:
                if src.startswith("vendor/"):
                    continue
                path = ROOT.parent.parent / src if src.startswith("lib/") else ROOT / src
                self.assertTrue(path.is_file(), src)
        self.assertIn("lib/songlink/model.py", cfg["engine"]["files"])

    def test_shell_references_existing_local_files(self):
        """sw.js caches the page and everything the page loads before its
        workers run, every one of those files exists, and app/precache.json
        (tools/gen_boot.py) lists the same shell."""
        shell = _sw_shell(_read("sw.js"))
        self.assertTrue(shell, "sw.js must define a SHELL array")
        for ref in shell:
            if ref != "./":
                self.assertTrue((ROOT / ref).is_file(), ref)
        needed = {"./index.html", "./manifest.json", "./icon-192.png", "./icon-512.png"}
        needed |= _page_imports(_read("index.html")) - {"./sw.js"}
        for script in ("workers/host.js", "workers/python_worker.js"):
            for ref in _worker_imports(_read(script)):
                if not ref.startswith("../vendor/"):      # vendored, so in precache.json
                    needed.add("./" + (Path(script).parent / ref).as_posix())
        self.assertEqual(needed - set(shell), set(), "missing from sw.js SHELL")
        precache = {p if p.startswith("./") else "./" + p for p in json.loads(_read("app/precache.json"))}
        self.assertEqual(set(shell) - precache, set(), "in sw.js SHELL but not app/precache.json")

    def test_product_sources_are_pinned(self):
        """The workers load exactly the wheels wheels.json pins, and MIDI
        comes from a tagged pydevices release."""
        wheels = json.loads(_read("wheels.json"))
        self.assertTrue(wheels)
        for name, pin in wheels.items():
            self.assertTrue(pin["file"].startswith("%s-%s-" % (name.replace("-", "_"), pin["version"])), name)
            self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")
        loaded = set(re.findall(r"vendor/wheels/([^\"]+\.whl)", _read("app/boot_config.js")))
        self.assertEqual(loaded, {pin["file"] for pin in wheels.values()})
        self.assertRegex(_read("scripts/fetch_midi.py"), r'PYDEVICES = "v\d+\.\d+\.\d+"')

    def test_python_runs_on_pyodide_only(self):
        """Python runs in Pyodide in the page's workers: no PyScript script
        tags on the page, and nothing loads MicroPython."""
        html = _read("index.html")
        self.assertNotIn('type="py"', html)
        self.assertNotIn('type="mpy"', html)
        self.assertIn("./workers/host.js", _page_imports(html))
        self.assertIn("../vendor/pyscript/pyodide/pyodide.mjs", _worker_imports(_read("workers/python_worker.js")))
        for name in PAGE_SCRIPTS:
            source = _read(name).lower()
            self.assertNotIn("micropython", source, name)
            self.assertNotIn("mpy", source, name)

    def test_install_is_left_to_the_browser(self):
        """No install button of our own, and nothing swallows the browser's
        own install prompt."""
        html = _read("index.html")
        self.assertNotIn('id="install"', html)
        self.assertNotIn("<button", html)
        for name in PAGE_SCRIPTS:
            source = _read(name)
            self.assertNotIn("beforeinstallprompt", source, name)
            self.assertNotIn(".prompt()", source, name)

    def test_referenced_local_assets_exist_after_vendoring(self):
        """Every local asset index.html/sw.js reference must exist somewhere
        in the tree, either checked in or produced by
        scripts/vendor_pyscript.sh. This is the regression test for a
        template that references vendor/ without ever creating it: run the
        vendor script (or skip if we're offline) before asserting.
        """
        vendor_dir = ROOT / "vendor" / "pyscript"
        if not vendor_dir.is_dir():
            script = ROOT / "scripts" / "vendor_pyscript.sh"
            try:
                subprocess.run(
                    [str(script)],
                    check=True,
                    cwd=ROOT,
                    capture_output=True,
                    timeout=120,
                )
            except Exception as exc:  # network unavailable, etc.
                self.skipTest(f"could not vendor PyScript to verify assets: {exc}")

        html_source = (ROOT / "index.html").read_text(encoding="utf-8")
        sw_source = (ROOT / "sw.js").read_text(encoding="utf-8")

        referenced = set(_local_asset_paths_from_html(html_source))

        shell_match = re.search(r"const SHELL = \[(.*?)\];", sw_source, re.S)
        self.assertIsNotNone(shell_match, "sw.js must define a SHELL array")
        for item in re.finditer(r"""['"](\./[^'"]+)['"]""", shell_match.group(1)):
            referenced.add(item.group(1))

        missing = []
        for ref in sorted(referenced):
            if ref in ("./", "./index.html"):
                continue
            candidate = ROOT / ref.lstrip("./")
            if not candidate.exists():
                missing.append(ref)

        self.assertFalse(missing, f"referenced assets missing from tree: {missing}")


if __name__ == "__main__":
    unittest.main()
