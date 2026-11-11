"""Data ingestion modules for global teleconnection indices and ERA5 local weather."""
from __future__ import annotations

from .cpc import download_cpc_indices, parse_cpc_cdas_csv
from .enso import download_nino34, parse_nino34_ascii
from .era5 import ERA5DownloadResult, download_weather, parse_open_meteo_era5_json
from .mjo import download_mjo, parse_bom_rmm_text, parse_psl_romi_text

__all__ = [
    "download_cpc_indices",
    "parse_cpc_cdas_csv",
    "download_nino34",
    "parse_nino34_ascii",
    "download_mjo",
    "parse_bom_rmm_text",
    "parse_psl_romi_text",
    "ERA5DownloadResult",
    "download_weather",
    "parse_open_meteo_era5_json",
]
