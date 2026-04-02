from __future__ import annotations

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter


class IndianaCFAdapter(BaseAdapter):
    source_name = "Indiana Campaign Finance"
    MANUAL_SEARCH_URL = "https://campaignfinance.in.gov/PublicSite/Homepage.aspx"
    BULK_DATA_URL = "https://campaignfinance.in.gov/PublicSite/Files/BulkData.aspx"

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        """
        Indiana's campaign finance system does not have a public JSON API.
        This adapter documents the absence and provides manual search links.
        """
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=[
                AdapterResult(
                    source_name=self.source_name,
                    source_url=self.MANUAL_SEARCH_URL,
                    entry_type="gap_documented",
                    title="Indiana CF: No automated access — manual search required",
                    body=(
                        f"Indiana campaign finance records for '{query}' "
                        f"require manual lookup. No public JSON API available. "
                        f"Manual search: {self.MANUAL_SEARCH_URL}. "
                        f"Bulk data download (CSV): {self.BULK_DATA_URL}. "
                        f"This gap is documented in the source check log."
                    ),
                    confidence="confirmed",
                    is_absence=True,
                )
            ],
            found=True,
        )
