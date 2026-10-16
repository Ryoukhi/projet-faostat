"""
Collecteur FAOSTAT Bulk Download
Télécharge les datasets FAOSTAT via le catalog JSON.
"""
import json
import logging
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Configuration ──────────────────────────────────────────────
CATALOG_URL = "https://bulks-faostat.fao.org/production/datasets_E.json"
BASE_RAW_DIR = Path("data/raw")
SAMPLE_DIR = Path("02_collecte/echantillons")
LOG_DIR = Path("02_collecte/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Correspondance DatasetCode -> dossier local
# ⚠️ À vérifier après avoir tiré le catalog
TARGET_DOMAINS = {
    "QCL": "faostat_production",
    "TCL":  "faostat_commerce",
    "PP":  "faostat_prices",
    "FBS": "faostat_food_balance",
    "RFN": "faostat_fertilizers",
    "RL":  "faostat_land_use",
}

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "faostat_collect.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def make_session() -> requests.Session:
    """Session HTTP avec retry automatique."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def fetch_catalog(session: requests.Session) -> list[dict]:
    """Télécharge et parse le catalog JSON FAOSTAT."""
    logger.info(f"Téléchargement du catalog : {CATALOG_URL}")
    resp = session.get(CATALOG_URL, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    # Structure : {"Datasets": {"-xmlns:xsi": "...", "Dataset": [...]}}
    datasets_node = payload.get("Datasets", {})
    if isinstance(datasets_node, dict):
        catalog = datasets_node.get("Dataset", [])
    elif isinstance(datasets_node, list):
        catalog = datasets_node
    else:
        catalog = []

    logger.info(f"Catalog chargé : {len(catalog)} datasets disponibles")
    return catalog


def find_dataset_url(catalog: list[dict], dataset_code: str) -> str | None:
    """Trouve l'URL ZIP pour un DatasetCode donné."""
    for entry in catalog:
        if entry.get("DatasetCode") == dataset_code:
            url = entry.get("FileLocation")
            rows = entry.get("FileRows", "?")
            size = entry.get("FileSize", "?")
            logger.info(
                f"  [{dataset_code}] {entry.get('DatasetName', '')[:60]} | "
                f"lignes={rows} | taille={size} | url=...{url[-60:]}"
            )
            return url
    logger.warning(f"  [{dataset_code}] INTROUVABLE dans le catalog")
    return None


def download_zip(session: requests.Session, url: str, output_dir: Path) -> Path:
    """Télécharge un ZIP en streaming avec vérification."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = url.split("/")[-1]
    dest = output_dir / filename

    if dest.exists():
        logger.info(f"  Déjà présent, on saute : {filename}")
        return dest

    logger.info(f"  Téléchargement : {filename}")
    resp = session.get(url, stream=True, timeout=300)
    resp.raise_for_status()

    tmp = dest.with_suffix(".zip.part")
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1 Mo
            f.write(chunk)
    tmp.rename(dest)

    size_mb = dest.stat().st_size / (1024 * 1024)
    logger.info(f"  Sauvegardé : {dest} ({size_mb:.1f} Mo)")
    return dest


def parse_and_sample(zip_path: Path, output_dir: Path, sample_n: int = 1000):
    """Lit le CSV dans le ZIP, log la structure, sauvegarde un échantillon."""
    logger.info(f"  Parsing : {zip_path.name}")

    with zipfile.ZipFile(zip_path) as zf:
        csv_files = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_files:
            logger.warning(f"  Aucun CSV trouvé dans {zip_path.name}")
            return None
        main_csv = csv_files[0]

        with zf.open(main_csv) as f:
            df = pd.read_csv(f, encoding="latin1", low_memory=False)

    logger.info(f"  Shape : {df.shape}")
    logger.info(f"  Colonnes : {list(df.columns)}")
    logger.info(f"  dtypes :\n{df.dtypes.to_string()}")

    # Échantillon
    sample_dir = SAMPLE_DIR / output_dir.name
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample = df.head(sample_n)
    sample.to_csv(sample_dir / f"{output_dir.name}_sample_{sample_n}.csv", index=False)
    logger.info(f"  Échantillon sauvegardé : {sample_dir}")

    return df


def main():
    session = make_session()
    catalog = fetch_catalog(session)
    results = {}

    for code, folder_name in TARGET_DOMAINS.items():
        logger.info(f"\n{'='*60}")
        logger.info(f"Traitement du domaine : {code} -> {folder_name}")

        url = find_dataset_url(catalog, code)
        if not url:
            results[code] = {"statut": "INTROUVABLE"}
            continue

        output_dir = BASE_RAW_DIR / folder_name
        try:
            zip_path = download_zip(session, url, output_dir)
            df = parse_and_sample(zip_path, output_dir)
            results[code] = {
                "statut": "OK",
                "zip": zip_path.name,
                "lignes": len(df) if df is not None else 0,
                "colonnes": len(df.columns) if df is not None else 0,
            }
        except Exception as e:
            logger.error(f"  ÉCHEC : {e}")
            results[code] = {"statut": "ECHEC", "erreur": str(e)}

        time.sleep(2)  # politesse envers le serveur

    # Rapport final
    logger.info(f"\n{'='*60}")
    logger.info("RAPPORT FINAL")
    for code, res in results.items():
        logger.info(f"  {code}: {res}")

    with open(LOG_DIR / "faostat_report.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()