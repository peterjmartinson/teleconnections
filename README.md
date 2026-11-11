# Teleconnection Climatology Builder (`teleconnections`)

Offline/occasional ETL builder that downloads historical teleconnection indices (`AO`, `NAO`, `PNA`, `NINO34`, `MJO`) and local ERA5 daily reanalysis weather (`1979–present`, including `2026` ERA5/ERA5T data) to precompute seasonal lagged local weather effects in SQLite (`~/database/climate.db`) for downstream consumption by **Screamsheet**.

## Architecture & Output Contract

- **Producer (`teleconnections`)**: Owns historical index series, ERA5 daily observations, $\pm 15$-day calendar-day normals, and seasonal lagged composite statistics across 12 rolling 3-month seasons (`DJF` through `NDJ`) and four lag windows (`0–5`, `6–10`, `11–15`, `16–20` days).
- **Consumer (`screamsheet`)**: Supplies current and forecast teleconnection states, queries `location`, `teleconnection_effect`, and `mjo_effect` read-only from `~/database/climate.db`, and automatically invokes `scripts/bootstrap_location.py` if a requested location has not been bootstrapped yet.

## Database Location

Follows the same path resolution order as `screamsheet`:
1. `TELECLIM_DB` environment variable (if set)
2. `database.path` in `config/settings.yaml` (if set)
3. Platform default: `~/database/climate.db` (`/home/peter/database/climate.db` on Linux)

## Usage

```bash
# 1. Initialize the 7-table v1 schema and seed teleconnection metadata
uv run python scripts/init_db.py

# 2. Download/update global teleconnection histories (AO, NAO, PNA, NINO34, MJO)
uv run python scripts/bootstrap_indices.py

# 3. Bootstrap a location's ERA5 history and teleconnection/MJO effects
uv run python scripts/bootstrap_location.py \
    --id bryn_mawr_pa \
    --name "Bryn Mawr, PA" \
    --lat 40.02 \
    --lon -75.34
```
