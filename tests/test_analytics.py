"""Run from repository root: python -m unittest discover -s tests -v."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pipeline", ROOT / "starter/starter.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def fixture(transfers, extra=()):
    tx = pd.DataFrame(transfers, columns=["src", "dst", "date", "sum_kzt"])
    tx["date"] = pd.to_datetime(tx.date)
    tx["sum_kzt"] = tx.sum_kzt.astype(float)
    ids = sorted(set(tx.src) | set(tx.dst) | set(extra))
    nodes = pd.DataFrame({"gid": ids, "depth": 1, "is_seed": False})
    edges = tx.groupby(["src", "dst"], as_index=False).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
    edges["depth"] = 1
    return edges, nodes, tx


class AnalyticsTests(unittest.TestCase):
    def test_duplicate_transfers_are_preserved(self):
        e, n, t = fixture([(1, 2, "2026-07-01", 5000)] * 2, extra=[3])
        p.validate_data(e, n, t)
        g = p.build_graph(e, n)
        f = p.calculate_features(g, n, e, t).set_index("gid")
        self.assertEqual(f.loc[2, "in_tx"], 2)
        self.assertEqual(f.loc[2, "in_kzt"], 10000)
        self.assertIn(3, g)

    def test_validation_rejects_bad_count_and_float_id(self):
        e, n, t = fixture([(1, 2, "2026-07-01", 5000)])
        bad = e.copy(); bad.loc[0, "n_tx"] = 2
        with self.assertRaises(ValueError): p.validate_data(bad, n, t)
        n["gid"] = n.gid.astype(float)
        with self.assertRaises(ValueError): p.validate_data(e, n, t)

    def test_fifo_does_not_reuse_money(self):
        e, n, t = fixture([(1, 2, "2026-07-01", 10000),
                           (2, 3, "2026-07-01", 7000),
                           (2, 4, "2026-07-02", 7000),
                           (2, 5, "2026-07-05", 5000)])
        f = p.calculate_features(p.build_graph(e, n), n, e, t).set_index("gid")
        self.assertEqual(f.loc[2, "quick_turnover"], 10000)

    def test_fifo_excludes_future_and_expired_incoming(self):
        e, n, t = fixture([(2, 3, "2026-07-01", 5000),
                           (1, 2, "2026-07-02", 5000),
                           (2, 3, "2026-07-05", 5000)])
        f = p.calculate_features(p.build_graph(e, n), n, e, t).set_index("gid")
        self.assertEqual(f.loc[2, "quick_turnover"], 0)

    def test_all_roles_precedence_and_boundaries(self):
        # Deliberately overlapping patterns test primary-role precedence.
        rows = [(2, 2, 2, False, False), (3, 5, 0, False, False),
                (1, 5, 0, False, False), (1, 1, 0, False, False),
                (1, 0, 0, False, False), (1, 0, 0, False, True),
                (1, 1, 0, True, False), (0, 0, 0, False, False)]
        f = pd.DataFrame(rows, columns=["in_deg", "out_deg", "seed_sources", "is_seed", "truncated_by_depth"])
        f["gid"] = range(1, 9)
        for col in ("pagerank", "authority_score", "hub_score", "quick_turnover_share"):
            f[col] = 0.0
        f["pass_through"] = 1.0
        f["out_kzt"] = f.out_deg * 5000
        roles, features = p.assign_roles(f, {i: 0 for i in range(1, 9)})
        self.assertEqual(roles.role.tolist(), ["coordinator", "consolidator", "distributor",
                         "transit", "terminal", "peripheral", "peripheral", "peripheral"])
        self.assertEqual(roles.iloc[-1].priority_score, 0)
        expected = features[["contribution_" + k for k in p.PRIORITY_WEIGHTS]].sum(axis=1) * features.priority_factor
        np.testing.assert_allclose(expected, features.priority_score)
        self.assertTrue(roles.evidence.str.len().le(200).all())

    def test_isolated_graph_clustering(self):
        n = pd.DataFrame({"gid": [1, 2], "depth": [0, 0], "is_seed": [True, True]})
        e = pd.DataFrame(columns=["src", "dst", "sum_kzt", "n_tx", "depth"])
        membership, clusters, _ = p.cluster_graph(p.build_graph(e, n), e, n)
        self.assertEqual(len(set(membership.values())), 2)
        self.assertEqual(clusters.sum_kzt_internal.sum(), 0)

    def test_real_dataset_outputs_and_repeatability(self):
        e, n, t = p.load_data(ROOT / "data")
        p.validate_data(e, n, t)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            p.run(ROOT / "data", out, ROOT / "starter/viewer.html")
            snapshots = {file.name: file.read_bytes() for file in out.iterdir()}
            roles = pd.read_csv(out / "nodes_roles.csv")
            clusters = pd.read_csv(out / "clusters.csv")
            top = pd.read_csv(out / "top_nodes.csv")
            payload = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(set(roles.gid), set(n.gid))
            self.assertTrue(roles.gid.is_unique)
            self.assertFalse(roles.isna().any().any())
            self.assertTrue(roles.role.isin(p.ROLES).all())
            self.assertTrue(roles.evidence.str.len().between(1, 200).all())
            for col in ("role_score", "priority_score"):
                self.assertTrue(roles[col].between(0, 1).all())
            for group in clusters.itertuples():
                ids = set(roles.loc[roles.cluster_id == group.cluster_id, "gid"])
                self.assertEqual(len(ids), group.n_nodes)
                self.assertEqual(int(n.loc[n.gid.isin(ids), "is_seed"].sum()), group.n_seed)
                self.assertAlmostEqual(e.loc[e.src.isin(ids) & e.dst.isin(ids), "sum_kzt"].sum(), group.sum_kzt_internal, places=2)
            expected = roles.sort_values(["priority_score", "gid"], ascending=[False, True]).head(50)
            self.assertEqual(top.gid.tolist(), expected.gid.tolist())
            self.assertEqual(top.role.tolist(), expected.role.tolist())
            self.assertTrue(top.why.str.contains("вклад").all())
            self.assertGreaterEqual(len(top), 20)
            for node in payload["nodes"]:
                self.assertIsInstance(node["gid"], str)
                if node["truncated_by_depth"] or node["is_seed"]:
                    self.assertNotEqual(node["role"], "terminal")
            self.assertEqual({v["gid"] for v in payload["nodes"]}, set(n.gid.astype(str)))
            self.assertTrue(all(isinstance(v["src"], str) and isinstance(v["dst"], str) for v in payload["edges"]))
            p.run(ROOT / "data", out, ROOT / "starter/viewer.html")
            self.assertEqual(snapshots, {file.name: file.read_bytes() for file in out.iterdir()})


if __name__ == "__main__":
    unittest.main()
