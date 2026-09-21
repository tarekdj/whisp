from whisp.stream import (
    leftover_text,
    match_chunk_case,
    spaced_chunk,
    update_stable,
    word_delta,
)


class TestUpdateStable:
    def test_prefix_growth_freezes_all_but_tentative(self):
        frozen, tentative = update_stable("", "hello world foo bar baz")
        assert frozen == "hello world foo"
        assert tentative == "bar baz"

    def test_grows_from_existing_frozen(self):
        frozen, tentative = update_stable("hello world", "hello world foo bar baz")
        assert frozen == "hello world foo"
        assert tentative == "bar baz"

    def test_case_tweaked_prefix_still_grows(self):
        # Frozen words keep their committed casing (already pasted); the
        # case-tweaked ASR prefix is still recognized and grown past.
        frozen, _ = update_stable("Hello world", "hello world foo bar baz")
        assert frozen == "Hello world foo"

    def test_never_shrinks_frozen(self):
        frozen, _ = update_stable("hello world foo bar", "hello world")
        assert frozen == "hello world foo bar"

    def test_trailing_window_overlap(self):
        # Sliding window: ASR returns only a tail that overlaps frozen words.
        frozen, tentative = update_stable(
            "one two three four five", "four five six seven eight"
        )
        assert frozen == "one two three four five six"
        assert tentative == "seven eight"

    def test_prompt_omitted_prefix_appends(self):
        # Whisper omitted the frozen prefix entirely: keep frozen, append new.
        frozen, _ = update_stable("one two three", "four five six")
        assert frozen.startswith("one two three")
        assert "four" in frozen

    def test_empty_asr_keeps_frozen(self):
        assert update_stable("hello world", "") == ("hello world", "")


class TestWordDelta:
    def test_returns_new_words(self):
        assert word_delta("hello world", "hello world foo bar") == "foo bar"

    def test_no_growth_returns_empty(self):
        assert word_delta("hello world", "hello world") == ""

    def test_case_insensitive_prefix(self):
        assert word_delta("Hello world", "hello world again") == "again"

    def test_disjoint_text_appended_with_tail(self):
        # tail=True (stream ticks): disjoint ASR is treated as an omitted
        # prefix + new tail, so the new words are returned.
        assert (
            word_delta("hello world", "completely different words here")
            == "completely different words here"
        )

    def test_disjoint_text_empty_without_tail(self):
        # tail=False (release path): no alignment, no delta.
        assert word_delta("hello world", "completely different", tail=False) == ""


class TestLeftoverText:
    def test_no_committed_returns_final(self):
        assert leftover_text("", "hello world") == "hello world"

    def test_tail_after_committed(self):
        assert leftover_text("hello world", "hello world foo bar") == "foo bar"

    def test_nothing_new_returns_empty(self):
        assert leftover_text("hello world", "hello world") == ""

    def test_empty_final_returns_empty(self):
        assert leftover_text("hello world", "") == ""

    def test_committed_appears_later_after_revision(self):
        # Final pass revised the opening; committed words appear mid-text.
        assert leftover_text("world foo", "oh hold on world foo bar") == "bar"

    def test_case_and_punctuation_insensitive(self):
        assert leftover_text("Hello, world", "hello world again") == "again"


class TestSpacedChunk:
    def test_first_chunk_no_leading_space(self):
        assert spaced_chunk(False, "hello") == "hello"

    def test_later_chunk_gets_leading_space(self):
        assert spaced_chunk(True, "world") == " world"

    def test_punctuation_start_no_space(self):
        assert spaced_chunk(True, ", world") == ", world"

    def test_empty_returns_empty(self):
        assert spaced_chunk(True, "  ") == ""


class TestMatchChunkCase:
    def test_mid_sentence_lowercase_restored(self):
        assert match_chunk_case(True, "world foo", "World foo") == "world foo"

    def test_first_chunk_keeps_capital(self):
        assert match_chunk_case(False, "hello", "Hello") == "Hello"

    def test_raw_capital_keeps_capital(self):
        assert match_chunk_case(True, "Paris is", "Paris is") == "Paris is"
