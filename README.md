python -c "
import requests
r = requests.get('https://bulks-faostat.fao.org/production/datasets_E.json')
data = r.json()
print('Type racine:', type(data).__name__)
if isinstance(data, dict):
    print('Clés racine:', list(data.keys()))
    items = data.get('datasets', [])
else:
    items = data
print('Nombre items:', len(items))
if items:
    print('Keys du 1er item:', list(items[0].keys()))
    print('Aperçu 1er item:', {k: str(items[0].get(k))[:80] for k in list(items[0].keys())[:8]})
"
Si jamais ton script rencontre un vrai 403 plus tard, la solution est d'ajouter un User-Agent dans make_session()

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (projet-faostat-etudiant)"
    })
    retries = Retry(...)
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session