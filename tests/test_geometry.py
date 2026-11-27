from pathlib import Path
import tempfile
import unittest
from hub import geometry as a

class GeometryTests(unittest.TestCase):
    def test_inspect_binding(self):
        i={"bytes":100,"sha256":"a"*64}
        r={**i,"status":"PASS","triangles":3,"promotion":"BLOCKED"}
        self.assertEqual(a.accept(r,[i],3),"PASS")
        for update in ({"sha256":"b"*64},{"bytes":101},{"triangles":4},{"promotion":"APPROVED"}):
            with self.assertRaises(ValueError):a.accept({**r,**update},[i],3)
    def test_compare_binding(self):
        i={"sha256":"a"*64};j={"sha256":"b"*64}
        r={"status":"PASS","promotion":"BLOCKED","sha256":[i["sha256"],j["sha256"]],
           "tolerance":1e-6,"numeric_equivalent":True,"document_equivalent":True,"binary_equal":False}
        self.assertEqual(a.accept(r,[i,j],1),"PASS")
        for update in ({"binary_equal":True},{"tolerance":.1},{"numeric_equivalent":False},{"document_equivalent":False}):
            with self.assertRaises(ValueError):a.accept({**r,**update},[i,j],1)
        self.assertEqual(a.accept({**r,"status":"FAIL","numeric_equivalent":False},[i,j],1),"FAIL")
    def test_recover_no_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            g=a.Geometry(Path(tmp)/"state")
            sid="a"*32
            g.save({"id":sid,"state":"RUNNING"},True)
            self.assertEqual(g.recover(sid)["state"],"INTERRUPTED")
            with self.assertRaises(ValueError):g.recover(sid)
if __name__=="__main__":unittest.main()
