"""Splitting a manuscript into chapters.

Pure functions, no database and no engine: what is checked here is where a chapter
boundary lands and what the splitter refuses. The two rules worth the most tests are
that no word is ever cut in half — a chapter that starts mid-word is a defect a
listener cannot unhear — and that the split is deterministic, because a customer who
sends the same manuscript twice should get the same book.
"""
from __future__ import annotations

import pytest

from hosted.control_plane import jobs, projects
from hosted.control_plane.projects import SplitError, split_chapters


def test_the_chapter_ceiling_is_inside_the_queues_own_per_job_ceiling():
    """The splitter must not produce chapters the queue would refuse.

    This is the coupling that makes the whole feature work, so it is read from both
    modules rather than restated: raising one without the other fails here.
    """
    assert projects.CHAPTER_MAX_CHARS <= jobs.MAX_JOB_CHARS
    assert projects.CHAPTER_TARGET_CHARS <= projects.CHAPTER_MAX_CHARS
    assert projects.PROJECT_MAX_CHARS >= projects.CHAPTER_MAX_CHARS


def test_a_short_manuscript_is_one_chapter():
    assert split_chapters("एक छोटा वाक्य।") == ["एक छोटा वाक्य।"]


def paragraph(marker: str, *, repeats: int = 40) -> str:
    """A paragraph of roughly 1,100 characters, well past half a chapter.

    Two of these cannot share a chapter, so a test built from them knows how many
    chapters to expect without restating the splitter's arithmetic.
    """
    return marker + " " + " ".join(["यह वाक्य इस अध्याय का है।"] * repeats)


def test_paragraphs_are_kept_whole_and_in_order():
    """A paragraph large enough to matter is never mixed with another, nor cut."""
    text = "\n\n".join([paragraph("पहला:"), paragraph("दूसरा:"), paragraph("तीसरा:")])
    chapters = split_chapters(text)
    assert chapters == [paragraph("पहला:"), paragraph("दूसरा:"), paragraph("तीसरा:")]


def test_a_long_manuscript_is_closed_at_the_target_and_never_above_the_ceiling():
    paragraph = "यह एक लंबा वाक्य है। " * 40  # ~880 characters per paragraph
    text = "\n\n".join([paragraph] * 6)
    chapters = split_chapters(text)
    assert len(chapters) > 1, "a manuscript well past the target must split"
    assert all(len(chapter) <= projects.CHAPTER_MAX_CHARS for chapter in chapters)
    # An interior chapter is closed only when the next paragraph would take it
    # past the target, so it holds at least half a chapter's worth. Without this a
    # splitter that closed after every paragraph would pass the ceiling check and
    # still turn a book into one-paragraph chapters.
    assert all(
        len(chapter) >= projects.CHAPTER_TARGET_CHARS // 2 for chapter in chapters[:-1]
    )


def test_no_word_is_lost_and_none_is_reordered():
    text = (
        "पहला वाक्य यहाँ है। दूसरा वाक्य यहाँ है।\n\n"
        + "शब्द " * 900
        + "\n\nअंतिम पैराग्राफ।"
    )
    chapters = split_chapters(text)
    assert " ".join(chapters).split() == text.split(), (
        "the split must preserve every word, in order, and add none"
    )


def test_a_paragraph_longer_than_a_chapter_is_cut_at_sentence_boundaries():
    long_paragraph = " ".join(["यह एक वाक्य है।"] * 400)  # one paragraph, no blank lines
    chapters = split_chapters(long_paragraph)
    assert len(chapters) > 1
    assert all(len(chapter) <= projects.CHAPTER_MAX_CHARS for chapter in chapters)
    for chapter in chapters[:-1]:
        assert chapter.endswith("।"), "an interior chapter ends where a sentence ends"


def test_a_sentence_longer_than_a_chapter_is_cut_between_words():
    sentence = " ".join(["शब्द"] * 3000) + "।"
    assert len(sentence) > projects.CHAPTER_MAX_CHARS
    chapters = split_chapters(sentence)
    assert len(chapters) > 1
    assert all(len(chapter) <= projects.CHAPTER_MAX_CHARS for chapter in chapters)
    assert " ".join(chapters).split() == sentence.split()


def test_a_single_word_longer_than_the_ceiling_is_refused_with_its_length():
    word = "x" * (projects.CHAPTER_MAX_CHARS + 1)
    with pytest.raises(SplitError) as error:
        split_chapters(f"before {word} after")
    message = str(error.value)
    assert str(len(word)) in message and str(projects.CHAPTER_MAX_CHARS) in message


def test_the_same_manuscript_always_splits_the_same_way():
    text = "।".join(["वाक्य"] * 500) + "\n\n" + "शब्द " * 300
    assert split_chapters(text) == split_chapters(text)


def test_extra_whitespace_around_paragraphs_does_not_change_the_chapters():
    text = "\n\n".join([paragraph("पहला:"), paragraph("दूसरा:")])
    messy = "\n\n\n  " + text.replace("\n\n", "\n\n   ") + "  \n"
    assert split_chapters(text) == split_chapters(messy)


def test_a_manuscript_shorter_than_the_target_is_one_whole_chapter():
    """Short text is not chopped up just because it has paragraph breaks."""
    text = "पहला पैराग्राफ।\n\nदूसरा पैराग्राफ।\n\nतीसरा पैराग्राफ।"
    assert split_chapters(text) == ["\n\n".join(["पहला पैराग्राफ।", "दूसरा पैराग्राफ।", "तीसरा पैराग्राफ।"])]


def test_no_chapter_is_empty_or_whitespace_only():
    text = "पहला।\n\n\n\nदूसरा।\n\n"
    chapters = split_chapters(text)
    assert all(chapter.strip() for chapter in chapters)


@pytest.mark.parametrize("blank", ["", "   ", "\n\n\t\n"])
def test_an_empty_manuscript_is_refused_rather_than_queued(blank):
    with pytest.raises(SplitError) as error:
        split_chapters(blank)
    assert "empty" in str(error.value)


def test_a_manuscript_with_too_many_chapters_is_refused_with_the_count():
    text = "\n\n".join(["छोटा।"] * 40)
    with pytest.raises(SplitError) as error:
        split_chapters(text, target_chars=4, max_chars=10, max_chapters=5)
    message = str(error.value)
    assert "40" in message and "5" in message


def test_limits_that_cannot_both_be_satisfied_are_refused():
    with pytest.raises(SplitError) as error:
        split_chapters("कुछ।", target_chars=100, max_chars=10)
    assert "ceiling" in str(error.value)


def test_devanagari_double_danda_counts_as_a_sentence_end():
    text = "॥".join(["श्लोक"] * 3000)
    chapters = split_chapters(text)
    assert len(chapters) > 1
    assert all(len(chapter) <= projects.CHAPTER_MAX_CHARS for chapter in chapters)


def test_a_manuscript_at_the_project_ceiling_is_still_splittable():
    """The route accepts up to ``PROJECT_MAX_CHARS``; that must be splittable.

    Checked against the constants rather than a 200,000-character literal, so the
    test stays cheap while still failing if the three limits stop fitting together.
    """
    chapter = "शब्द " * (projects.CHAPTER_TARGET_CHARS // 5)
    needed = -(-projects.PROJECT_MAX_CHARS // len(chapter))  # ceil division
    assert needed <= projects.CHAPTER_MAX_COUNT, (
        "PROJECT_MAX_CHARS can no longer be split into CHAPTER_MAX_COUNT chapters; "
        "the two constants have drifted apart"
    )
    chapters = split_chapters(chapter * needed)
    assert len(chapters) <= projects.CHAPTER_MAX_COUNT