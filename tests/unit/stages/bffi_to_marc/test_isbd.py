"""Tests for ISBD punctuation rules."""

from bffi_pipeline.stages.bffi_to_marc.isbd import get_isbd_punctuation


class TestGetIsbdPunctuation:
    """Test the get_isbd_punctuation function."""

    # --- Core behavior -------------------------------------------------------

    def test_disabled_returns_empty(self) -> None:
        """When enabled=False, return empty string."""
        assert get_isbd_punctuation("260", "a", "b", enabled=False) == ""

    def test_unknown_tag_returns_empty(self) -> None:
        """When tag has no rules, return empty string."""
        assert get_isbd_punctuation("999", "a", None, enabled=True) == ""

    def test_unknown_subfield_returns_empty(self) -> None:
        """When subfield code has no rules, return empty string."""
        assert get_isbd_punctuation("260", "z", None, enabled=True) == ""

    def test_unknown_next_subfield_returns_empty(self) -> None:
        """When next subfield has no rule, return empty string."""
        assert get_isbd_punctuation("260", "a", "z", enabled=True) == ""

    # --- 260 / 264 (Publication) ---------------------------------------------

    def test_260_a_with_b(self) -> None:
        """260 $a followed by $b: space-colon."""
        assert get_isbd_punctuation("260", "a", "b", enabled=True) == " :"

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

    def test_264_tag_agnostic(self) -> None:
        """264 uses same rules as 260."""
        assert get_isbd_punctuation("264", "a", "b", enabled=True) == " :"
        assert get_isbd_punctuation("264", "b", "c", enabled=True) == ","
        assert get_isbd_punctuation("264", "c", None, enabled=True) == "."

    # --- 100 / 700 (Personal name) -------------------------------------------

    def test_100_a_with_e(self) -> None:
        """100 $a followed by $e: comma."""
        assert get_isbd_punctuation("100", "a", "e", enabled=True) == ","

    def test_100_e_last(self) -> None:
        """100 $e alone: period."""
        assert get_isbd_punctuation("100", "e", None, enabled=True) == "."

    def test_100_4_last(self) -> None:
        """100 $4 alone: period."""
        assert get_isbd_punctuation("100", "4", None, enabled=True) == "."

    def test_100_a_with_t(self) -> None:
        """100 $a followed by $t (analytical title): comma."""
        assert get_isbd_punctuation("100", "a", "t", enabled=True) == ","

    def test_100_t_last(self) -> None:
        """100 $t alone: period."""
        assert get_isbd_punctuation("100", "t", None, enabled=True) == "."

    def test_700_tag_agnostic(self) -> None:
        """700 uses same rules as 100."""
        assert get_isbd_punctuation("700", "a", "e", enabled=True) == ","
        assert get_isbd_punctuation("700", "e", None, enabled=True) == "."

    # --- 110 / 710 (Corporate body) ------------------------------------------

    def test_110_a_with_e(self) -> None:
        """110 $a followed by $e: comma."""
        assert get_isbd_punctuation("110", "a", "e", enabled=True) == ","

    def test_110_e_last(self) -> None:
        """110 $e alone: period."""
        assert get_isbd_punctuation("110", "e", None, enabled=True) == "."

    def test_110_d_with_4(self) -> None:
        """110 $d followed by $4: comma."""
        assert get_isbd_punctuation("110", "d", "4", enabled=True) == ","

    def test_710_tag_agnostic(self) -> None:
        """710 uses same rules as 110."""
        assert get_isbd_punctuation("710", "a", "e", enabled=True) == ","
        assert get_isbd_punctuation("710", "e", None, enabled=True) == "."

    # --- 111 / 711 (Meeting) -------------------------------------------------

    def test_111_a_with_e(self) -> None:
        """111 $a followed by $e: comma."""
        assert get_isbd_punctuation("111", "a", "e", enabled=True) == ","

    def test_111_e_last(self) -> None:
        """111 $e alone: period."""
        assert get_isbd_punctuation("111", "e", None, enabled=True) == "."

    def test_711_tag_agnostic(self) -> None:
        """711 uses same rules as 111."""
        assert get_isbd_punctuation("711", "a", "e", enabled=True) == ","
        assert get_isbd_punctuation("711", "e", None, enabled=True) == "."

    # --- 245 (Title) ---------------------------------------------------------

    def test_245_a_with_b(self) -> None:
        """245 $a followed by $b: space-colon."""
        assert get_isbd_punctuation("245", "a", "b", enabled=True) == " :"

    def test_245_a_with_n(self) -> None:
        """245 $a followed by $n (part number): space-colon."""
        assert get_isbd_punctuation("245", "a", "n", enabled=True) == " :"

    def test_245_a_with_p(self) -> None:
        """245 $a followed by $p (part name): space-colon."""
        assert get_isbd_punctuation("245", "a", "p", enabled=True) == " :"

    def test_245_n_with_p(self) -> None:
        """245 $n followed by $p: space-colon."""
        assert get_isbd_punctuation("245", "n", "p", enabled=True) == " :"

    def test_245_p_with_b(self) -> None:
        """245 $p followed by $b: space-colon."""
        assert get_isbd_punctuation("245", "p", "b", enabled=True) == " :"

    def test_245_b_with_c(self) -> None:
        """245 $b followed by $c: slash."""
        assert get_isbd_punctuation("245", "b", "c", enabled=True) == "/"

    def test_245_b_with_f(self) -> None:
        """245 $b followed by $f: slash."""
        assert get_isbd_punctuation("245", "b", "f", enabled=True) == "/"

    def test_245_b_last(self) -> None:
        """245 $b alone: period."""
        assert get_isbd_punctuation("245", "b", None, enabled=True) == "."

    def test_245_c_last(self) -> None:
        """245 $c alone: period."""
        assert get_isbd_punctuation("245", "c", None, enabled=True) == "."

    def test_245_a_last(self) -> None:
        """245 $a alone: empty."""
        assert get_isbd_punctuation("245", "a", None, enabled=True) == ""

    # --- 300 (Physical description) ------------------------------------------

    def test_300_a_with_b(self) -> None:
        """300 $a followed by $b: space-colon."""
        assert get_isbd_punctuation("300", "a", "b", enabled=True) == " :"

    def test_300_a_with_c(self) -> None:
        """300 $a followed by $c: space-semicolon."""
        assert get_isbd_punctuation("300", "a", "c", enabled=True) == " ;"

    def test_300_a_with_e(self) -> None:
        """300 $a followed by $e: space-plus."""
        assert get_isbd_punctuation("300", "a", "e", enabled=True) == " +"

    def test_300_b_with_c(self) -> None:
        """300 $b followed by $c: space-semicolon."""
        assert get_isbd_punctuation("300", "b", "c", enabled=True) == " ;"

    def test_300_b_with_e(self) -> None:
        """300 $b followed by $e: space-plus."""
        assert get_isbd_punctuation("300", "b", "e", enabled=True) == " +"

    def test_300_b_last(self) -> None:
        """300 $b alone: period."""
        assert get_isbd_punctuation("300", "b", None, enabled=True) == "."

    def test_300_c_with_e(self) -> None:
        """300 $c followed by $e: space-plus."""
        assert get_isbd_punctuation("300", "c", "e", enabled=True) == " +"

    def test_300_c_last(self) -> None:
        """300 $c alone: period."""
        assert get_isbd_punctuation("300", "c", None, enabled=True) == "."

    def test_300_e_last(self) -> None:
        """300 $e alone: period."""
        assert get_isbd_punctuation("300", "e", None, enabled=True) == "."

    # --- Notes (single $a) ---------------------------------------------------

    def test_500_a_last(self) -> None:
        """500 $a alone: period."""
        assert get_isbd_punctuation("500", "a", None, enabled=True) == "."

    def test_504_a_last(self) -> None:
        """504 $a alone: period."""
        assert get_isbd_punctuation("504", "a", None, enabled=True) == "."

    def test_511_a_last(self) -> None:
        """511 $a alone: period."""
        assert get_isbd_punctuation("511", "a", None, enabled=True) == "."

    def test_534_a_last(self) -> None:
        """534 $a alone: period."""
        assert get_isbd_punctuation("534", "a", None, enabled=True) == "."

    def test_546_a_last(self) -> None:
        """546 $a alone: period."""
        assert get_isbd_punctuation("546", "a", None, enabled=True) == "."

    # --- 490 (Series) --------------------------------------------------------

    def test_490_a_with_v(self) -> None:
        """490 $a followed by $v: period."""
        assert get_isbd_punctuation("490", "a", "v", enabled=True) == "."

    def test_490_a_with_x(self) -> None:
        """490 $a followed by $x: period."""
        assert get_isbd_punctuation("490", "a", "x", enabled=True) == "."

    def test_490_v_last(self) -> None:
        """490 $v alone: period."""
        assert get_isbd_punctuation("490", "v", None, enabled=True) == "."

    def test_490_v_with_x(self) -> None:
        """490 $v followed by $x: period."""
        assert get_isbd_punctuation("490", "v", "x", enabled=True) == "."

    def test_490_x_last(self) -> None:
        """490 $x alone: period."""
        assert get_isbd_punctuation("490", "x", None, enabled=True) == "."

    # --- 650 / 651 (Subjects) ------------------------------------------------

    def test_650_a_with_t(self) -> None:
        """650 $a followed by $t: period."""
        assert get_isbd_punctuation("650", "a", "t", enabled=True) == "."

    def test_650_a_with_v(self) -> None:
        """650 $a followed by $v: period."""
        assert get_isbd_punctuation("650", "a", "v", enabled=True) == "."

    def test_650_a_with_0(self) -> None:
        """650 $a followed by $0: period."""
        assert get_isbd_punctuation("650", "a", "0", enabled=True) == "."

    def test_650_a_last(self) -> None:
        """650 $a alone: period."""
        assert get_isbd_punctuation("650", "a", None, enabled=True) == "."

    def test_650_t_last(self) -> None:
        """650 $t alone: period."""
        assert get_isbd_punctuation("650", "t", None, enabled=True) == "."

    def test_650_v_with_x(self) -> None:
        """650 $v followed by $x: period."""
        assert get_isbd_punctuation("650", "v", "x", enabled=True) == "."

    def test_650_v_last(self) -> None:
        """650 $v alone: period."""
        assert get_isbd_punctuation("650", "v", None, enabled=True) == "."

    def test_650_x_with_y(self) -> None:
        """650 $x followed by $y: period."""
        assert get_isbd_punctuation("650", "x", "y", enabled=True) == "."

    def test_650_x_last(self) -> None:
        """650 $x alone: period."""
        assert get_isbd_punctuation("650", "x", None, enabled=True) == "."

    def test_650_y_with_0(self) -> None:
        """650 $y followed by $0: period."""
        assert get_isbd_punctuation("650", "y", "0", enabled=True) == "."

    def test_650_y_last(self) -> None:
        """650 $y alone: period."""
        assert get_isbd_punctuation("650", "y", None, enabled=True) == "."

    def test_650_0_with_2(self) -> None:
        """650 $0 followed by $2: period."""
        assert get_isbd_punctuation("650", "0", "2", enabled=True) == "."

    def test_650_0_last(self) -> None:
        """650 $0 alone: period."""
        assert get_isbd_punctuation("650", "0", None, enabled=True) == "."

    def test_650_2_last(self) -> None:
        """650 $2 alone: period."""
        assert get_isbd_punctuation("650", "2", None, enabled=True) == "."

    def test_651_tag_agnostic(self) -> None:
        """651 uses same rules as 650."""
        assert get_isbd_punctuation("651", "a", "v", enabled=True) == "."
        assert get_isbd_punctuation("651", "v", "x", enabled=True) == "."
        assert get_isbd_punctuation("651", "2", None, enabled=True) == "."

    # --- 250 / 306 / 334 / 338 / 353 / 740 (single $a) -----------------------

    def test_250_a_last(self) -> None:
        """250 $a alone: period."""
        assert get_isbd_punctuation("250", "a", None, enabled=True) == "."

    def test_306_a_last(self) -> None:
        """306 $a alone: period."""
        assert get_isbd_punctuation("306", "a", None, enabled=True) == "."

    def test_334_a_last(self) -> None:
        """334 $a alone: period."""
        assert get_isbd_punctuation("334", "a", None, enabled=True) == "."

    def test_740_a_last(self) -> None:
        """740 $a alone: period."""
        assert get_isbd_punctuation("740", "a", None, enabled=True) == "."

    # --- 336 / 337 (RDA content/media) ---------------------------------------

    def test_336_a_with_b(self) -> None:
        """336 $a followed by $b: colon."""
        assert get_isbd_punctuation("336", "a", "b", enabled=True) == ":"

    def test_336_a_with_2(self) -> None:
        """336 $a followed by $2: period."""
        assert get_isbd_punctuation("336", "a", "2", enabled=True) == "."

    def test_336_b_last(self) -> None:
        """336 $b alone: period."""
        assert get_isbd_punctuation("336", "b", None, enabled=True) == "."

    def test_336_2_last(self) -> None:
        """336 $2 alone: period."""
        assert get_isbd_punctuation("336", "2", None, enabled=True) == "."

    def test_337_a_with_b(self) -> None:
        """337 $a followed by $b: colon."""
        assert get_isbd_punctuation("337", "a", "b", enabled=True) == ":"

    def test_337_a_with_2(self) -> None:
        """337 $a followed by $2: period."""
        assert get_isbd_punctuation("337", "a", "2", enabled=True) == "."

    def test_337_b_last(self) -> None:
        """337 $b alone: period."""
        assert get_isbd_punctuation("337", "b", None, enabled=True) == "."

    def test_337_2_last(self) -> None:
        """337 $2 alone: period."""
        assert get_isbd_punctuation("337", "2", None, enabled=True) == "."

    # --- 338 (RDA carrier) ---------------------------------------------------

    def test_338_a_with_b(self) -> None:
        """338 $a followed by $b: colon."""
        assert get_isbd_punctuation("338", "a", "b", enabled=True) == ":"

    def test_338_a_with_c(self) -> None:
        """338 $a followed by $c: colon."""
        assert get_isbd_punctuation("338", "a", "c", enabled=True) == ":"

    def test_338_a_with_2(self) -> None:
        """338 $a followed by $2: period."""
        assert get_isbd_punctuation("338", "a", "2", enabled=True) == "."

    def test_338_b_with_c(self) -> None:
        """338 $b followed by $c: colon."""
        assert get_isbd_punctuation("338", "b", "c", enabled=True) == ":"

    def test_338_b_with_2(self) -> None:
        """338 $b followed by $2: period."""
        assert get_isbd_punctuation("338", "b", "2", enabled=True) == "."

    def test_338_c_last(self) -> None:
        """338 $c alone: period."""
        assert get_isbd_punctuation("338", "c", None, enabled=True) == "."

    def test_338_2_last(self) -> None:
        """338 $2 alone: period."""
        assert get_isbd_punctuation("338", "2", None, enabled=True) == "."

    # --- 353 (Supplementary content) -----------------------------------------

    def test_353_a_last(self) -> None:
        """353 $a alone: period."""
        assert get_isbd_punctuation("353", "a", None, enabled=True) == "."

    # --- 730 (Uniform title) -------------------------------------------------

    def test_730_a_with_b(self) -> None:
        """730 $a followed by $b: period."""
        assert get_isbd_punctuation("730", "a", "b", enabled=True) == "."

    def test_730_a_with_f(self) -> None:
        """730 $a followed by $f: colon."""
        assert get_isbd_punctuation("730", "a", "f", enabled=True) == ":"

    def test_730_b_last(self) -> None:
        """730 $b alone: period."""
        assert get_isbd_punctuation("730", "b", None, enabled=True) == "."

    def test_730_f_with_g(self) -> None:
        """730 $f followed by $g: comma."""
        assert get_isbd_punctuation("730", "f", "g", enabled=True) == ","

    def test_730_f_last(self) -> None:
        """730 $f alone: period."""
        assert get_isbd_punctuation("730", "f", None, enabled=True) == "."

    def test_730_g_last(self) -> None:
        """730 $g alone: period."""
        assert get_isbd_punctuation("730", "g", None, enabled=True) == "."
