from whisp.cleanup import _cleanup_faithful


def test_rejects_assistant_answer():
    orig = "Can you please commit and push"
    bad = "Sure let's push"
    assert not _cleanup_faithful(orig, bad)


def test_accepts_punctuation_and_casing():
    orig = "can you please commit and push"
    good = "Can you please commit and push?"
    assert _cleanup_faithful(orig, good)


def test_accepts_filler_removal():
    orig = "um can you commit"
    good = "Can you commit?"
    assert _cleanup_faithful(orig, good)
