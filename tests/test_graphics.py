import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hub import graphics as g
from hub.orchestrator import git, digest
from test_orchestrator import repo

# Synthetic fixture; not the actual private Factory or a native render.
REQ = {"id": "fixture", "version": "0.1.0", "era": "2012-inspired",
       "triangle_budget": 8000, "texture_max_px": 1024, "author": "Fixture",
       "dimensions_m": {"width": 1.8, "height": 1.1, "length": 4.8}}
STUB = '''
import json
def read_request(path):
    r=json.loads(path.read_text())
    if r.get("id") != "fixture":
        raise ValueError("request rejected")
    return r
def plan(path):
    r=json.loads(path.read_text())["jobs"][0]
    return {"schema":1,"executed":False,"jobs":[{"id":r["id"],"status":"PLANNED","output":"staging/asset.glb"}]}
'''


class GraphicsTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.checkout = self.root / "factory"
        repo(self.checkout, "assets")
        for rel in g.REQUIRED:
            p = self.checkout / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(STUB if rel == g.REQUIRED[0] else "{}")
        subprocess.run(["git","-C",str(self.checkout),"add","."],check=True)
        subprocess.run(["git","-C",str(self.checkout),"commit","-qm","fixture"],check=True)
        self.sha = git(self.checkout,"rev-parse","HEAD")
        self.req = self.root / "request.json"
        self.req.write_text(json.dumps(REQ))
        self.g = g.Graphics(self.root / "state")
        self.pin = patch.object(g,"FACTORY_SHA",self.sha)
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def run_plan(self):
        return self.g.run(str(self.checkout),self.sha,str(self.req))

    def test_real_preflight_and_persistence(self):
        result = self.run_plan()
        self.assertEqual(result["state"],"PASS")
        self.assertEqual(result["native"],"NOT_RUN")
        self.assertEqual(result["promotion"],"BLOCKED")
        self.assertEqual(g.Graphics(self.root/"state").get(result["id"]),result)
        for item in result["evidence"]:
            self.assertEqual(digest(self.root/"state"/("graphics-"+result["id"])/item["path"]),item["sha256"])

    def test_bad_request_and_dirty_checkout(self):
        self.req.write_text(json.dumps({**REQ,"id":"bad"}))
        self.assertEqual(self.run_plan()["state"],"BLOCKED")
        (self.checkout/"extra").touch()
        with self.assertRaises(ValueError):
            self.run_plan()

    def test_wrong_revision_and_runtime(self):
        with self.assertRaises(ValueError):
            self.g.run(str(self.checkout),"a"*40,str(self.req))
        result=self.g.run(str(self.checkout),self.sha,str(self.req),build=True,
                          blender="/missing/blender",godot="/missing/godot",
                          blender_sha="a"*64,godot_sha="b"*64)
        self.assertEqual(result["state"],"BLOCKED")
        self.assertNotEqual(result["native"],"PASS")

    def test_unsafe_evidence_and_plan(self):
        root=self.root/"evidence";root.mkdir()
        outside=self.root/"outside";outside.write_text("x")
        (root/"link").symlink_to(outside)
        with self.assertRaises(ValueError):
            g.file_evidence(root,"link",100)
        with self.assertRaises(ValueError):
            g.accept_plan({"request":REQ,"plan":{"schema":1,"executed":True,"jobs":[]}},REQ)

    def test_receipt_acceptance_and_tamper(self):
        # Deliberately synthetic bytes and markers: exercises receipt checks, not GLB/PNG decoding.
        work=self.root/"native";work.mkdir()
        (work/"staging").mkdir()
        asset=work/"staging/asset.glb";asset.write_bytes(b"synthetic evidence")
        image=work/"staging/capture.png";image.write_bytes(b"synthetic capture")
        (work/"request.json").write_text(json.dumps(REQ))
        record={"sha256":digest(asset),"bytes":asset.stat().st_size,
                "mesh_triangles":2952,"textures":0,"structural_qa":"PASS",
                "advanced_validation":{"status":"PASS","sha256":digest(asset),"bytes":asset.stat().st_size,"triangles":2952,"promotion":"BLOCKED"},
                "attribute_presence":"PASS","godot_import":"PASS","promotion":"BLOCKED"}
        report={"factory_version":"0.1.5","status":"BUILT","approval":"PENDING","promotion":"BLOCKED",
                "stages":{**{k:"PASS" for k in g.STAGES[:5]},"manual_review":"NOT_RUN","promotion":"BLOCKED"},
                "asset":record,"godot_fixture":{"meshes":28,"size":[1.8,1.1,4.8]},
                "blender":"Blender 5.3.0 fixture","godot":"4.7.2.fixture",
                "capture":{"sha256":digest(image),"bytes":image.stat().st_size,"width":960,"height":540}}
        registry={"id":REQ["id"],"version":REQ["version"],"author":REQ["author"],
                  "license":"ORIGINAL","origin":"original procedural","dependencies":[],
                  "approval":"PENDING","promotion":"BLOCKED","asset":record,"status":"BUILT",
                  "request_sha256":digest(work/"request.json")}
        (work/"report.json").write_text(json.dumps(report))
        (work/"registry.json").write_text(json.dumps(registry))
        for name in ("plan.json","blender-version.log","godot-version.log","blender.log",
                     "godot-import.log","godot-test.log","godot-capture.log"):
            (work/name).write_text("synthetic fixture")
        self.assertEqual(g.accept_build(work,REQ,True)[0],report)
        del report["asset"]["advanced_validation"]
        (work/"report.json").write_text(json.dumps(report))
        with self.assertRaises(ValueError):g.accept_build(work,REQ,True)
        asset.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            g.accept_build(work,REQ,True)

    def test_reject_fake_native_report(self):
        work=self.root/"native";work.mkdir()
        (work/"report.json").write_text(json.dumps({"status":"BUILT","approval":"PENDING","promotion":"BLOCKED"}))
        (work/"registry.json").write_text("{}")
        with self.assertRaises(ValueError):
            g.accept_build(work,REQ,False)


if __name__=="__main__":
    unittest.main()
