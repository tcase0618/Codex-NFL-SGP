from research.forward_calibrate import calibrate_by_week,summarize_week


def test_missing_calibrated_samples_are_unknown_not_zero_error():
    summary=summarize_week([{'week':1,'outcome':1,'calibrated_probability':''}],1)
    assert summary['resolved']==0
    assert summary['raw_brier'] is None and summary['hit_rate'] is None


def test_week_ordering_does_not_certify_input_availability():
    training=[{'market':'rushing_yards','probability':.2 if i%2==0 else .8,'outcome':i%2} for i in range(120)]
    rows,log=calibrate_by_week([{'week':1,'market':'rushing_yards','probability':.6,'outcome':1}],training)
    assert rows[0]['calibration_status']=='week_ordered_reconstruction_unverified_inputs'
    assert 'unverified' in log[0]['chronology_scope']
