"""Tests for ISBD punctuation rules."""

from bffi_pipeline.stages.bffi_to_marc.isbd import get_isbd_punctuation


class TestGetIsbdPunctuation:
    """Test the get_isbd_punctuation function."""

    def test_disabled_returns_empty(self) -> None:
        """When enabled=False, return empty string."""
        assert get_isbd_punctuation("260", "a", "b", enabled=False) == ""

    def test_unknown_tag_returns_empty(self) -> None:
        """When tag has no rules, return empty string."""
        assert get_isbd_punctuation("999", "a", None, enabled=True) == ""

    def test_260_a_with_b(self) -> None:
        """260 $a followed by $b: colon."""
        assert get_isbd_punctuation("260", "a", "b", enabled=True) == ":"

    def test_260_a_with_c(self) -> None:
        """260 $a followed by $c: comma."""
        assert get_isbd_punctuation("260", "a", "c", enabled=True) == ","

    def test_260_a_last(self) -> None:
        """260 $a alone: empty."""
        assert get_isbd_punctuation("260", "a", None, enabled=True) == ""

    def test_260_b_with_c(self) -> None:
        """260 $b followed by $c: comma."""
        assert get_isbd_punctuation("260", "b", "c", enabled=True) == ","

    def test_260_b_last(self) -> None:
        """260 $b alone: empty."""
        assert get_isbd_punctuation("260", "b", None, enabled=True) == ""

    def test_260_c_last(self) -> None:
        """260 $c alone: period."""
        assert get_isbd_punctuation("260", "c", None, enabled=True) == "."

    def test_100_a_with_e(self) -> None:
        """100 $a followed by $e: comma."""
        assert get_isbd_punctuation("100", "a", "e", enabled=True) == ","

    def test_100_e_last(self) -> None:
        """100 $e alone: period."""
        assert get_isbd_punctuation("100", "e", None, enabled=True) == "."

    def test_245_a_with_b(self) -> None:
        """245 $a followed by $b: colon."""
        assert get_isbd_punctuation("245", "a", "b", enabled=True) == ":"

    def test_245_b_with_c(self) -> None:
        """245 $b followed by $c: slash."""
        assert get_isbd_punctuation("245", "b", "c", enabled=True) == "/"

    def test_245_b_last(self) -> None:
        """245 $b alone: period."""
        assert get_isbd_punctuation("245", "b", None, enabled=True) == "."

    def test_245_c_last(self) -> None:
        """245 $c alone: period."""
        assert get_isbd_punctuation("245", "c", None, enabled=True) == "."

    def test_500_a_last(self) -> None:
        """500 $a alone: period."""
        assert get_isbd_punctuation("500", "a", None, enabled=True) == "."

    def test_490_a_with_v(self) -> None:
        """490 $a followed by $v: period."""
        assert get_isbd_punctuation("490", "a", "v", enabled=True) == "."

    def test_490_v_last(self) -> None:
        """490 $v alone: period."""
        assert get_isbd_punctuation("490", "v", None, enabled=True) == "."
