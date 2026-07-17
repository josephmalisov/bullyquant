from lab.store import Store


def test_campaign_generation_roundtrip():
    s = Store(":memory:")
    cid = s.create_campaign("obj", {"a": 1})
    c = s.get_campaign(cid)
    assert c["objective"] == "obj"
    assert c["params"] == {"a": 1}

    gid = s.add_generation(cid, 1, name="Test", hypothesis="h", code="print(1)")
    s.update_generation(gid, status="scored", score=0.42,
                        stats={"Sharpe Ratio": "1.2"},
                        score_breakdown={"score": 0.42},
                        validation={"verdict": "clean", "flags": []})
    g = s.get_generation(gid)
    assert g["score"] == 0.42
    assert g["stats"] == {"Sharpe Ratio": "1.2"}
    assert g["validation"]["verdict"] == "clean"


def test_leaderboard_orders_by_score():
    s = Store(":memory:")
    cid = s.create_campaign("obj")
    for i, sc in enumerate([0.1, 0.9, 0.5], start=1):
        gid = s.add_generation(cid, i, name=f"g{i}")
        s.update_generation(gid, score=sc)
    lb = s.leaderboard(cid)
    assert [g["score"] for g in lb] == [0.9, 0.5, 0.1]


def test_usage_totals():
    s = Store(":memory:")
    cid = s.create_campaign("obj")
    s.record_usage(cid, None, "frontier", "claude-fable-5", 1000, 200)
    s.record_usage(cid, None, "frontier", "claude-fable-5", 500, 100)
    s.record_usage(cid, None, "cheap", "claude-haiku-4-5", 300, 50)
    totals = {r["model"]: r for r in s.usage_totals(cid)}
    assert totals["claude-fable-5"]["input_tokens"] == 1500
    assert totals["claude-fable-5"]["calls"] == 2
    assert totals["claude-haiku-4-5"]["output_tokens"] == 50
