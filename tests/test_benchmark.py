import copy
from pathlib import Path
import tempfile
import unittest
from hub import benchmark as b

def fixture(mode="render"):
    return {"schema":1,"mode":mode,"profile":"eco","cars":24,"warmup":60,"asset":False,
            "samples":[10000]*114+[20000]*6,"viewport":[1920,1080],"adapter":"NVIDIA GeForce GTX 1650",
            "vendor":"NVIDIA","renderer":"gl_compatibility","display":"headless" if mode=="headless" else "X11",
            "draw_calls_peak":0 if mode=="headless" else 120}
class BenchmarkTests(unittest.TestCase):
    def test_frame_statistics(self):
        r=b.stats(fixture()["samples"])
        self.assertEqual(r["p95_ms"],10)
        self.assertEqual(r["p99_ms"],20)
        self.assertEqual(r["over_16_67ms"],6)
        self.assertAlmostEqual(r["observed_loop_fps"],1000/10.5)
        for values in ([0]*120,[float("nan")]*120,[True]*120,[1]*119):
            with self.assertRaises(ValueError):b.stats(values)
    def test_headless_not_fps(self):
        r=b.accept(fixture("headless"),"headless","eco",120,False)
        self.assertIsNone(r["observed_loop_fps"])
        self.assertEqual(r["frame_budget"],"NOT_RUN")
    def test_render_identity_and_targets(self):
        d=fixture()
        self.assertEqual(b.accept(d,"render","eco",120,False)["frame_budget"],"60_FPS_TARGET")
        for change in ({"viewport":[960,540]},{"cars":23},{"draw_calls_peak":0},{"display":"headless"},{"profile":"balanced"},{"asset":True}):
            with self.assertRaises(ValueError):b.accept({**d,**change},"render","eco",120,False)
    def test_gpu_not_inferred(self):
        d=fixture()
        self.assertEqual(b.gpu_summary("render",d,[])["classification"],"INCONCLUSIVE")
        self.assertEqual(b.gpu_summary("software",{**d,"adapter":"llvmpipe"},[])["classification"],"SOFTWARE")
        self.assertEqual(b.gpu_summary("headless",d,[])["classification"],"NOT_RUN")
        t=[{"devices":[{"name":"NVIDIA GeForce GTX 1650","driver":"fixture","vram_used_mib":500,"utilization_pct":50}]}]
        self.assertEqual(b.gpu_summary("render",d,t)["classification"],"NVIDIA_OBSERVED")
        self.assertEqual(b.gpu_summary("render",{**d,"adapter":"other"},t)["classification"],"INCONCLUSIVE")
    def test_compare_reject_mismatch(self):
        a={"state":"PASS","fixture_sha256":"a","godot_sha256":"b","asset_sha256":None,"mode":"render","profile":"eco",
           "samples":120,"hardware":{"adapter":"fixture"},"metrics":{"p95_ms":10}}
        c={**a,"metrics":{"p95_ms":12}}
        self.assertAlmostEqual(b.compare(a,c)["p95_change_percent"],20)
        with self.assertRaises(ValueError):b.compare(a,{**c,"profile":"balanced"})
        with self.assertRaises(ValueError):b.compare({**a,"mode":"headless"},{**c,"mode":"headless"})
    def test_recover_no_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            x=b.Bench(Path(tmp)/"state");sid="a"*32
            x.save({"id":sid,"state":"RUNNING"},True)
            self.assertEqual(x.recover(sid)["state"],"INTERRUPTED")
            with self.assertRaises(ValueError):x.recover(sid)
if __name__=="__main__":unittest.main()
