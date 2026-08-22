# app/database.py
#
# Ovaj modul je zaduzen za KONEKCIJU ka PostgreSQL bazi.
#
# Kljucni koncept: CONNECTION POOL
# ---------------------------------
# Otvaranje konekcije ka bazi nije besplatno - traje neko vreme
# (rukovanje, autentifikacija...). Kad bismo za SVAKI HTTP zahtev
# otvarali novu konekciju i posle je gasili, aplikacija bi bila spora
# i baza bi se "gusila" pod naletom konekcija.
#
# Umesto toga, "pool" (bazen) na startu otvori npr. 5-10 konekcija
# i drzi ih otvorenim. Kad aplikaciji zatreba konekcija, "pozajmi"
# jednu iz bazena, iskoristi je, i VRATI je nazad (ne gasi je).
# Sledeci zahtev je ponovo koristi. Mnogo brze i efikasnije.

from psycopg_pool import ConnectionPool
from app.config import settings

# Connection string - jedan string koji sadrzi sve podatke potrebne
# da se PostgreSQL kontaktira (host, port, ime baze, korisnik, lozinka).
CONNECTION_STRING = (
    f"host={settings.db_host} "
    f"port={settings.db_port} "
    f"dbname={settings.db_name} "
    f"user={settings.db_user} "
    f"password={settings.db_password}"
)

# Kreiramo pool JEDNOM, pri pokretanju aplikacije. min_size/max_size
# odredjuju koliko konekcija pool drzi otvorenih (za pocetak, 10 je
# vise nego dovoljno za licnu upotrebu).
pool = ConnectionPool(
    conninfo=CONNECTION_STRING,
    min_size=2,
    max_size=10,
    open=True,
)


def get_connection():
    """
    Generator funkcija koja "pozajmljuje" konekciju iz pool-a.

    'yield' je Python kljucna rec koja pravi tzv. generator - funkcija
    se "zamrzne" na yield liniji, preda konekciju onome ko je pozvao,
    i kad taj zavrsi posao, nastavlja izvrsavanje ISPOD yield linije
    (u ovom slucaju, nema nista ispod - 'with' blok automatski vraca
    konekciju u pool cim se izadje iz njega, cak i ako se desi greska).

    Ovo koristimo kao FastAPI "dependency" - videces u main.py kako
    se ovo automatski poziva za svaki HTTP zahtev koji treba bazu.
    """
    with pool.connection() as conn:
        yield conn
