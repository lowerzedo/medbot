import pytest

from scripts.evaluate_documents import word_errors


@pytest.mark.parametrize(
    "expected,actual,errors,words",
    [
        ("DOB: 1970-01-02", "dob:   1970-01-02", 0, 2),
        ("DOB: 1970-01-02", "DOB: 1978-01-02", 1, 2),
        ("DOB: 1970-01-02", "DOB:", 1, 2),
        ("DOB: 1970-01-02", "DOB: 1970-01-02 extra", 1, 2),
    ],
)
def test_word_error_rate_counts_transcription_edits(
    expected: str, actual: str, errors: int, words: int
) -> None:
    assert word_errors(expected, actual) == (errors, words)
