"""LDA public filing URLs: lda.gov supersedes lda.senate.gov for detail pages."""

from adapters.lda import LDA_API_FILINGS_URL, lda_public_filing_url


def test_lda_api_base_is_lda_gov() -> None:
    assert "lda.gov" in LDA_API_FILINGS_URL
    assert "api/v1/filings" in LDA_API_FILINGS_URL


def test_lda_public_filing_url_uses_print_path() -> None:
    u = "9e630716-62c7-4ba2-b27c-ff29afb887f4"
    assert lda_public_filing_url(u) == (
        "https://lda.gov/filings/public/filing/9e630716-62c7-4ba2-b27c-ff29afb887f4/print/"
    )
