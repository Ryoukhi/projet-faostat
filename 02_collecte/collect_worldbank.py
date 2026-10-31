"""
Collecteur World Bank Pink Sheet
Télécharge et parse le fichier Excel des prix des matières premières.
"""
import logging
import time
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Configuration ──────────────────────────────────────────────
PINK_SHEET_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "5d903e848db1d1b83e0ec8f744e55570-0350012021/related/"
    "CMO-Historical-Data-Monthly.xlsx"
)
BASE_RAW_DIR = Path("data/raw/worldbank_pink_sheet")
SAMPLE_DIR = Path("02_collecte/echantillons/worldbank_pink_sheet")
LOG_DIR = Path("02_collecte/logs")

BASE_RAW_DIR.mkdir(parents=True, exist_ok=True)
SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "worldbank_collect.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def make_session() -> requests.Session:
    """Session HTTP avec retry et User-Agent."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (projet-faostat-etudiant/1.0)"
    })
    retries = Retry(
        total=5,
        backoff_factor=3,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def download_pink_sheet(session: requests.Session) -> Path:
    """Télécharge le fichier XLSX du Pink Sheet."""
    dest = BASE_RAW_DIR / "CMO-Historical-Data-Monthly.xlsx"

    if dest.exists():
        logger.info(f"Déjà présent : {dest} ({dest.stat().st_size / 1024 / 1024:.1f} Mo)")
        return dest

    logger.info(f"Téléchargement : {PINK_SHEET_URL}")
    resp = session.get(PINK_SHEET_URL, stream=True, timeout=300)
    resp.raise_for_status()

    tmp = dest.with_suffix(".xlsx.part")
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    tmp.rename(dest)

    size_mb = dest.stat().st_size / (1024 * 1024)
    logger.info(f"Sauvegardé : {dest} ({size_mb:.1f} Mo)")
    return dest


def parse_pink_sheet(xlsx_path: Path) -> pd.DataFrame:
    """
    Parse le Pink Sheet avec gestion des 5 lignes d'en-tête.

    Structure :
      - Lignes 0-3 : titre, description
      - Ligne 4    : noms des produits (en-tête réel)
      - Ligne 5    : unités ($/bbl, $/mt, etc.)
      - Lignes 6+  : données mensuelles
    """
    logger.info(f"Parsing : {xlsx_path.name}")

    # Lire les noms des feuilles disponibles
    xls = pd.ExcelFile(xlsx_path)
    logger.info(f"Feuilles disponibles : {xls.sheet_names}")

    # Trouver la feuille "Monthly Prices"
    sheet_name = None
    for name in xls.sheet_names:
        if "monthly" in name.lower() and "price" in name.lower():
            sheet_name = name
            break

    if sheet_name is None:
        sheet_name = xls.sheet_names[0]  # fallback
        logger.warning(f"Feuille 'Monthly Prices' non trouvée, utilisation de '{sheet_name}'")

    logger.info(f"Lecture de la feuille : {sheet_name}")

    # Ligne 4 (index 4) = noms des produits, ligne 5 (index 5) = unités
    # On lit à partir de la ligne 4 (header=4)
    df = pd.read_excel(
        xlsx_path,
        sheet_name=sheet_name,
        header=4,          # ligne 5 Excel = index 4
        skiprows=None,
        engine="openpyxl",
    )

    # La première colonne est "Month" ou "Unnamed: 0"
    first_col = df.columns[0]
    df = df.rename(columns={first_col: "Month"})

    # Supprimer la première ligne (unités) qui a été lue comme donnée
    # Car header=4 lit la ligne 5 (noms), mais la ligne 6 (unités) devient la 1ère ligne de données
    df = df.iloc[1:].reset_index(drop=True)

    logger.info(f"Shape brute : {df.shape}")
    logger.info(f"Colonnes (premières 10) : {list(df.columns[:10])}")

    # Nettoyage : supprimer les colonnes entièrement vides
    df = df.dropna(axis=1, how="all")

    # Nettoyage des noms de colonnes (espaces, caractères spéciaux)
    df.columns = [str(c).strip() for c in df.columns]

    # Convertir la colonne Month (ex. "1960M01") en datetime
    df["Month"] = pd.to_datetime(df["Month"], format="%YM%m", errors="coerce")

    # Les valeurs sont des strings avec virgule décimale -> convertir en float
    for col in df.columns:
        if col != "Month":
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            )

    logger.info(f"Shape après nettoyage : {df.shape}")
    logger.info(f"Période : {df['Month'].min()} -> {df['Month'].max()}")

    return df


def save_sample(df: pd.DataFrame, sample_n: int = 1000):
    """Sauvegarde un échantillon pour inspection rapide."""
    sample = df.head(sample_n)
    sample_path = SAMPLE_DIR / f"pink_sheet_sample_{sample_n}.csv"
    sample.to_csv(sample_path, index=False)
    logger.info(f"Échantillon sauvegardé : {sample_path}")


def save_full_parquet(df: pd.DataFrame):
    """
    Sauvegarde le DataFrame complet en Parquet.
    Format colonne, compressé, idéal pour l'ETL.
    """
    parquet_path = BASE_RAW_DIR / "pink_sheet_clean.parquet"
    df.to_parquet(parquet_path, index=False, compression="snappy")
    size_mb = parquet_path.stat().st_size / (1024 * 1024)
    logger.info(f"Parquet sauvegardé : {parquet_path} ({size_mb:.1f} Mo)")


def main():
    session = make_session()

    # 1. Téléchargement
    xlsx_path = download_pink_sheet(session)

    # 2. Parsing
    df = parse_pink_sheet(xlsx_path)

    # 3. Vérification rapide
    logger.info(f"\n{'='*60}")
    logger.info("APERÇU DES DONNÉES")
    logger.info(f"  Lignes : {len(df)}")
    logger.info(f"  Colonnes : {len(df.columns)}")
    logger.info(f"  Produits : {list(df.columns[1:15])}...")
    logger.info(f"\n5 premières lignes :\n{df.head().to_string()}")

    # 4. Sauvegardes
    save_sample(df)
    save_full_parquet(df)

    logger.info(f"\n{'='*60}")
    logger.info("RAPPORT FINAL")
    logger.info(f"  Fichier source : {xlsx_path}")
    logger.info(f"  Lignes : {len(df)}")
    logger.info(f"  Colonnes : {len(df.columns)}")
    logger.info(f"  Période : {df['Month'].min()} -> {df['Month'].max()}")
    logger.info(f"  Produits agricoles : {len([c for c in df.columns if any(k in c.lower() for k in ['wheat', 'maize', 'rice', 'soy', 'palm', 'coffee', 'cocoa', 'sugar', 'cotton'])])}")


if __name__ == "__main__":
    main()