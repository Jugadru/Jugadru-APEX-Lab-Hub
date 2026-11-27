import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub import research as r

SPEC={"hypothesis":"Exact equivalence","sources":["REF-105","IDEA-033"],
      "config":{"seed":42},"criteria":{"mismatch":{"op":"eq","threshold":0,"unit":"count"}}}
NAME="ordered_commands"

def report(status="PASS"):
    return {"id":"EXP-TEST","experiment":NAME,"revision":r.ENGINEERING_SHA,"hypothesis":SPEC["hypothesis"],
            "sources":SPEC["sources"],"configuration":SPEC["config"],"origin":"SYNTHETIC",
            "promotion":"NOT_AUTHORIZED","scientific_validation":"NOT_PERFORMED","gameplay_validation":"NOT_RUN",
            "reported_status":status,"artifact_bytes":"MATCH","plan_sha256":"a"*64,"result_sha256":"b"*64,
            "metrics":[{"metric":"mismatch",**SPEC["criteria"]["mismatch"],"observed":0 if status=="PASS" else 1,
                        "criterion_met":status=="PASS"}],"observations":{"comparison":"synthetic"},"limitations":"Fixture only"}

class ResearchTests(unittest.TestCase):
    def test_reviewed_capability_groups(self):
        self.assertEqual(len(r.PHYSICS_AND_LEGACY),14)
        self.assertEqual(len(r.RACECRAFT),17)
        self.assertEqual(len(r.STRATEGY),3)
        self.assertEqual(len(r.BUILTINS),34)
        self.assertNotIn("historical_pace",r.BUILTINS)
        self.assertIn("racecraft_control_refinement",r.BUILTINS)
        self.assertIn("strategy_weather_prediction",r.BUILTINS)

    def test_catalog_exact_blob_binding(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"f";e=Path(tmp)/"e"
            (f/"docs/references").mkdir(parents=True);(e/"research").mkdir(parents=True)
            for name in r.FOUNDATION_CATALOG_PIN["files"]:(f/"docs/references"/name).write_text("{}")
            declared={"schema":1,"repository":r.REPOSITORIES["foundation"],**r.FOUNDATION_CATALOG_PIN,
                      "counts":{"references":198,"possibilities":120}}
            r.write(e/"research/foundation-pin.json",declared)
            with patch.object(r,"git",side_effect=list(r.FOUNDATION_CATALOG_PIN["files"].values())):
                self.assertEqual(r.catalog_binding(f,e)["status"],"MATCH")
            with patch.object(r,"git",return_value="0"*40):
                with self.assertRaises(ValueError):r.catalog_binding(f,e)

    def test_frozen_spec(self):
        self.assertEqual(r.check_spec(SPEC),SPEC)
        for change in ({"sources":["REF-105","REF-105"]},{"config":{"seed":True}},
                       {"criteria":{"fps":{"op":"ge","threshold":60,"unit":"fps"}}}):
            with self.assertRaises(ValueError):
                r.check_spec({**SPEC,**change})

    def test_report_binding(self):
        self.assertEqual(r.accept_report(report(),NAME,SPEC,"EXP-TEST")["reported_status"],"PASS")
        for patch_value in ({"revision":"a"*40},{"promotion":"AUTHORIZED"},{"configuration":{"seed":1}},
                            {"artifact_bytes":"NOT_RUN"},{"hypothesis":"changed"},{"id":"EXP-OTHER"}):
            with self.assertRaises(ValueError):
                r.accept_report({**report(),**patch_value},NAME,SPEC,"EXP-TEST")
        bad=report();bad["metrics"][0]["threshold"]=1
        with self.assertRaises(ValueError):
            r.accept_report(bad,NAME,SPEC,"EXP-TEST")

    def test_adverse_result_preserved(self):
        failed=r.accept_report(report("FAIL"),NAME,SPEC,"EXP-TEST")
        result=r.comparison([report(),failed])
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(len(result["experiments"]),2)
        self.assertEqual(result["experiments"][1]["metrics"][0]["observed"],1)
        self.assertEqual(r.comparison([])["status"],"INCONCLUSIVE")

    def test_persistent_plan_tamper_and_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); f=root/"f";e=root/"e";f.mkdir();e.mkdir()
            case=r.Research(root/"registry")
            def catalog(_,work):
                work.mkdir(parents=True,exist_ok=True)
                return {NAME:SPEC},{"ranking":[]}
            def packet(_,work,sources):
                work.mkdir()
                return {"idea_plans":{"IDEA-033":{"owner_module":"core2","steps":["audit"],"reference_ids":["REF-105"]}}}
            with patch.object(case,"roots",return_value=(f,e)),patch.object(r,"engineering_catalog",side_effect=catalog),patch.object(r,"foundation_packet",side_effect=packet),patch.object(r,"equery",return_value={"foundation_links":"MATCH","promotion":"NOT_AUTHORIZED"}),patch.object(r,"unchanged"),patch.object(r,"catalog_binding",return_value={"status":"MATCH"}):
                plan=case.prepare(f,e,[NAME],"Compare")
                self.assertEqual(plan["state"],"PLANNED")
                with self.assertRaises(ValueError):
                    case.run(plan["id"],f,e,"a"*64)
                def completed(_,work,name,spec,exp_id):
                    return {**report(),"id":exp_id}
                with patch.object(r,"execute_fixture",side_effect=completed):
                    final=case.run(plan["id"],f,e,plan["plan_sha256"])
                self.assertEqual(final["state"],"PASS")
                self.assertEqual(r.Research(root/"registry").get(plan["id"]),final)
                self.assertEqual(len(case.history(plan["id"])),5)
                with self.assertRaises(ValueError):
                    case.run(plan["id"],f,e,plan["plan_sha256"])

    def test_modified_plan_blocks_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); case=r.Research(root/"state")
            sid="a"*32;work=case.store.root/("research-"+sid);work.mkdir()
            p=work/"frozen-plan.json";p.write_text("{}")
            from hub.orchestrator import digest
            h=digest(p);case.save({"id":sid,"state":"PLANNED","plan_sha256":h},"fixture",True)
            p.write_text('{"changed":true}')
            with patch.object(case,"roots",return_value=(root,root)):
                with self.assertRaises(ValueError):
                    case.run(sid,root,root,h)

if __name__=="__main__":
    unittest.main()
