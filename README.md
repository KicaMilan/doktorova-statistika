# doktorova statistika

Aplikacija za unos i analizu fudbalskih rezultata — lige, sezone,
timovi, mecevi, i izvestaji (tabela lige, sumarna statistika po kolu,
detaljan pregled po timu). Licni projekat, ujedno i nacin da se
digitalizuje i unapredi rucno vodjena statistika koju je moj tast
godinama beleziyao.

## Arhitektura

- **Baza**: PostgreSQL 16, na zasebnoj VM (Rocky Linux 9)
- **Backend**: Python / FastAPI, raw SQL preko psycopg3 (namerno bez
  ORM-a, radi lakseg ucenja SQL-a), na zasebnoj VM (Ubuntu 24.04)
- **Frontend**: server-rendered HTML (Jinja2), bez JS frameworka
- **Deployment**: systemd servis + nginx reverse proxy

## Struktura foldera

```
doktorova_statistika/
├── app/                    # FastAPI aplikacija
│   ├── main.py              # rute (API + HTML stranice)
│   ├── config.py             # citanje .env konfiguracije
│   ├── database.py           # konekcija ka bazi (connection pool)
│   ├── schemas.py            # Pydantic modeli za validaciju
│   └── templates/            # Jinja2 HTML sabloni
├── sql/                     # SQL migracije i upiti (istorijski, redosled izvrsavanja)
├── nginx/                   # nginx server-block konfiguracija (referenca)
├── systemd/                 # systemd unit fajl (referenca)
├── requirements.txt
└── .env.example             # sablon konfiguracije (bez stvarnih lozinki)
```

## Pokretanje (od nule)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# upisi stvarne podatke o konekciji ka bazi u .env

# pokreni SQL fajlove iz sql/ folfera REDOM (01, 02, ...) protiv
# prazne PostgreSQL baze da postavis semu

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Napomena o nginx/ i systemd/ folderima

Fajlovi u ovim folderima su REFERENTNE kopije - stvarno se koriste sa
sistemskih lokacija (`/etc/nginx/sites-available/...` i
`/etc/systemd/system/...`). Drzimo ih ovde radi verzionisanja i
lakseg oporavka ako se server ikad ponovo postavlja od nule.
