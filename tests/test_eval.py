from tennishl.eval import evaluate


def seg(s, e):
    return {"start_s": s, "end_s": e}


def test_perfect_match():
    truth = [seg(5, 10), seg(20, 30)]
    r = evaluate(truth, truth)
    assert r.recall == 1.0 and r.precision == 1.0
    assert r.mean_start_error_s == 0.0


def test_missed_and_false_positive():
    truth = [seg(5, 10), seg(20, 30), seg(50, 55)]
    detected = [seg(5.5, 10.5), seg(70, 75)]
    r = evaluate(truth, detected)
    assert r.matched == 1
    assert len(r.missed) == 2
    assert len(r.false_positives) == 1
    assert abs(r.recall - 1 / 3) < 1e-9
    assert r.precision == 0.5
    assert r.mean_start_error_s == 0.5


def test_partial_overlap_below_half_does_not_match():
    truth = [seg(10, 20)]
    detected = [seg(18, 30)]  # 2 s overlap of a 10 s point
    r = evaluate(truth, detected)
    assert r.matched == 0


def test_one_detection_cannot_match_two_truths():
    truth = [seg(10, 14), seg(15, 19)]
    detected = [seg(10, 19)]  # merged two points into one
    r = evaluate(truth, detected)
    assert r.matched == 1 and len(r.missed) == 1
