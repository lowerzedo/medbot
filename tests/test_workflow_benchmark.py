from scripts.evaluate_workflow import evaluate_case, load_cases, run_benchmark


def test_benchmark_executes_frozen_workflows_and_reports_counts() -> None:
    report = run_benchmark()
    assert report["passed"] is True
    assert report["case_count"] == 10
    metrics = report["metrics"]
    assert metrics["accepted_field_precision"]["numerator"] > 0
    assert metrics["false_ready_rate"]["numerator"] == 0
    assert metrics["false_ready_rate"]["denominator"] > 0


def test_benchmark_detects_incorrect_expected_value_and_state() -> None:
    case = next(case for case in load_cases() if case.case_id == "synthetic-wrong-field-citation")
    fields = {**case.expected_fields, "patient.date_of_birth": "1984-02-03"}
    incorrect = case.model_copy(
        update={"expected_fields": fields, "expected_status": "READY_TO_ROUTE"}
    )
    result = evaluate_case(incorrect)
    assert result["passed"] is False
    assert result["checks"]["accepted_fields_match"] is False
    assert result["checks"]["processing_state_matches"] is False
