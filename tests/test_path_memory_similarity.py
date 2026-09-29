"""src/path_memory.py の類似検索（similarity / search_entries）の回帰テスト。

文字bigram＋コサイン類似度で、全角半角・大文字小文字・区切り文字の揺れや
1文字の誤字を吸収して登録済みパスを見つけられることを固定化する。
"""

from pathlib import Path

import pytest

from src import path_memory


@pytest.fixture
def registry(tmp_path):
    pm_dir = tmp_path / "pm"

    def _register(*paths: str) -> Path:
        for p in paths:
            path_memory.register("t1", p, pm_dir, 500)
        return pm_dir

    return _register


class TestSimilarity:
    def test_substring_is_full_score(self) -> None:
        assert path_memory.similarity("報告書", r"C:\data\月次報告書.xlsx") == 1.0

    def test_absorbs_width_case_and_separator(self) -> None:
        assert path_memory.similarity("ﾎｳｺｸ", r"C:\data\ホウコク.txt") == 1.0
        assert path_memory.similarity("REPORT.XLSX", r"C:\data\report.xlsx") == 1.0
        assert path_memory.similarity("C:/data/report.xlsx", r"C:\data\report.xlsx") == 1.0

    def test_one_char_typo_scores_higher_than_unrelated(self) -> None:
        typo = path_memory.similarity("月次報告署.xlsx", r"C:\data\月次報告書.xlsx")
        unrelated = path_memory.similarity("月次報告署.xlsx", r"C:\data\notes.txt")
        assert typo >= 0.5
        assert unrelated < 0.3

    def test_filename_weight_changes_score(self) -> None:
        path = r"C:\projects\月次報告書\data.xlsx"
        low = path_memory.similarity("報告書.xlsx", path, filename_weight=0.0)
        high = path_memory.similarity("報告書.xlsx", path, filename_weight=1.0)
        assert low != high

    def test_empty_query_is_zero(self) -> None:
        assert path_memory.similarity("", r"C:\data\a.txt") == 0.0


class TestSearchEntries:
    def test_ranks_by_similarity_and_excludes_below_threshold(self, registry) -> None:
        pm_dir = registry(r"C:\data\notes.txt", r"C:\data\月次報告書.xlsx", r"C:\data\週次報告.xlsx")

        hits = path_memory.search_entries("t1", "月次報告署.xlsx", pm_dir)

        assert [h["index"] for h in hits][0] == 2
        assert all(h["index"] != 1 for h in hits)
        assert all(0.3 <= h["score"] <= 1.0 for h in hits)

    def test_top_k_limits_results(self, registry) -> None:
        pm_dir = registry(*[rf"C:\data\report_{i}.txt" for i in range(10)])

        hits = path_memory.search_entries("t1", "report", pm_dir, top_k=3)

        assert len(hits) == 3

    def test_no_registry_returns_empty(self, tmp_path) -> None:
        assert path_memory.search_entries("t1", "x", tmp_path / "none") == []
