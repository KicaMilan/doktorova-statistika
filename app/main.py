# app/main.py
#
# Ovo je "ulazna tacka" aplikacije - fajl koji se pokrece da bi
# server oziveo. Ovde definisemo ENDPOINT-e: URL adrese na koje
# browser (ili bilo koji klijent) moze da posalje zahtev, i sta
# aplikacija treba da odgovori.

from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg import Connection
from psycopg.errors import UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException
from psycopg.rows import dict_row
from pydantic import ValidationError

from app.database import get_connection
from app.schemas import (
    MecCreate, MecUpdate, MecOut,
    LigaCreate, SezonaCreate, TimCreate, TimLigaSezonaCreate,
)

# Kreiramo INSTANCU FastAPI aplikacije. Ovaj objekat "app" je ono sto
# uvicorn (server, iz requirements.txt) pokrece i "oslu\u0161kuje" na mrezi.
app = FastAPI(title="doktorova_statistika API")

# Jinja2Templates "zna" gde da trazi .html fajlove (folder templates/,
# koji smo napravili unutar app/) i kako da ih popuni podacima.
templates = Jinja2Templates(directory="app/templates")


# Annotated[...] ovde pravi "prijatan" tip koji kombinuje: "ovo je
# psycopg Connection objekat" + "FastAPI, molim te automatski pozovi
# get_connection() funkciju i ubaci rezultat ovde". Ovo se zove
# DEPENDENCY INJECTION - ne moras rucno da otvaras/zatvaras konekciju
# u SVAKOM endpoint-u, FastAPI to radi za tebe pozivajuci get_connection.
DbConnection = Annotated[Connection, Depends(get_connection)]


# @app.get("/health") je DEKORATOR - kaze FastAPI-ju "kad neko posalje
# GET HTTP zahtev na adresu /health, pozovi funkciju ispod".
@app.get("/health")
def health_check():
    """
    Najjednostavniji mogu\u0107i endpoint - ne dira bazu, samo potvrdjuje
    da je aplikacija uopste upaljena i da odgovara. Koristan za prvu
    proveru da li server radi, pre nego sto sumnjas na konekciju ka bazi.
    """
    return {"status": "ok"}


@app.get("/timovi")
def lista_timova(conn: DbConnection):
    """
    Prvi "pravi" endpoint - vraca sve timove iz baze.

    conn: DbConnection      -> FastAPI automatski "ubrizga" konekciju
                                (preko get_connection iz database.py)

    cursor(row_factory=dict_row) -> kazemo psycopg-u da nam redove iz
    baze vraca kao Python RECNIKE ({"id": 1, "naziv": "Milan", ...})
    umesto kao obicne torke (1, "Milan", ...) - lakse se pretvara u JSON.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, naziv, grad
            FROM public.tim
            ORDER BY naziv
        """)
        timovi = cur.fetchall()

    # FastAPI automatski pretvara Python listu/recnik u JSON odgovor -
    # ne moramo mi rucno da radimo tu konverziju.
    return {"timovi": timovi}


# =====================================================================
# CRUD za mec (Create, Read, Update, Delete)
# =====================================================================
# CRUD je skracenica koja opisuje 4 osnovne operacije nad podacima -
# gotovo svaki "entitet" u aplikaciji (mec, tim, liga...) ce na kraju
# imati ove iste 4 operacije, samo prilagodjene svom obliku podataka.

MEC_KOLONE = """
    id, liga_id, sezona_id, domacin_id, gost_id, kolo, datum,
    ht_domacin_golovi, ht_gost_golovi, ft_domacin_golovi, ft_gost_golovi
"""


@app.get("/mecevi", response_model=list[MecOut])
def lista_meceva(conn: DbConnection):
    """READ (svi) - vraca sve unete meceve, najnoviji (najveci id) prvi."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {MEC_KOLONE} FROM public.mec ORDER BY id DESC")
        return cur.fetchall()


@app.get("/mecevi/{mec_id}", response_model=MecOut)
def jedan_mec(mec_id: int, conn: DbConnection):
    """READ (jedan) - {mec_id} u putanji URL-a se automatski "uhvati"
    kao Python parametar iste imena, i FastAPI ga automatski pretvara
    u int (ako posaljes npr. /mecevi/abc, dobices gresku pre nego sto
    ova funkcija uopste bude pozvana)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {MEC_KOLONE} FROM public.mec WHERE id = %s", (mec_id,))
        mec = cur.fetchone()

    if mec is None:
        # HTTPException je nacin da endpoint "prekine" izvrsavanje i
        # odmah vrati gresku klijentu, sa HTTP status kodom po izboru.
        # 404 = "Not Found", standardni kod kad trazeni resurs ne postoji.
        raise HTTPException(status_code=404, detail=f"Mec sa id={mec_id} ne postoji")
    return mec


@app.post("/mecevi", response_model=MecOut, status_code=201)
def unesi_mec(podaci: MecCreate, conn: DbConnection):
    """
    CREATE - unosi novi mec.

    podaci: MecCreate  -> FastAPI ocekuje JSON telo HTTP zahteva,
    automatski ga proverava protiv MecCreate modela (ukljucujuci nas
    @model_validator), i tek ako sve prodje, poziva ovu funkciju.

    status_code=201 -> "Created", standardni HTTP kod za uspesno
    kreiranje novog resursa (umesto podrazumevanog 200).
    """
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"""
                INSERT INTO public.mec (
                    liga_id, sezona_id, domacin_id, gost_id, kolo, datum,
                    ht_domacin_golovi, ht_gost_golovi,
                    ft_domacin_golovi, ft_gost_golovi
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {MEC_KOLONE}
            """, (
                podaci.liga_id, podaci.sezona_id,
                podaci.domacin_id, podaci.gost_id,
                podaci.kolo, podaci.datum,
                podaci.ht_domacin_golovi, podaci.ht_gost_golovi,
                podaci.ft_domacin_golovi, podaci.ft_gost_golovi,
            ))
            novi_mec = cur.fetchone()
        # commit() trajno upisuje promenu u bazu. Bez ovoga, INSERT
        # bi ostao "na cekanju" u transakciji i ne bi se stvarno
        # sacuvao - vazan koncept, objasnicu detaljnije ako zatreba.
        conn.commit()
        return novi_mec

    except (UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException) as greska:
        # Ako baza ODBIJE unos (npr. neki CHECK constraint iz seme
        # koji smo napravili na pocetku, ili FK koji ne postoji),
        # PostgreSQL vraca gresku, psycopg je "prevede" u Python
        # izuzetak, mi ga hvatamo ovde i vracamo razumljivu HTTP
        # gresku umesto da aplikacija "puca" sa 500.
        conn.rollback()  # ponisti nedovrsenu transakciju
        raise HTTPException(status_code=400, detail=str(greska))


@app.put("/mecevi/{mec_id}", response_model=MecOut)
def izmeni_mec(mec_id: int, podaci: MecUpdate, conn: DbConnection):
    """UPDATE - menja postojeci mec. Salje se CEO objekat ponovo
    (svih 10 polja), ne samo ono sto se menja - to je razlika izmedju
    PUT (zameni ceo resurs) i PATCH (izmeni samo navedena polja)."""
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"""
                UPDATE public.mec
                SET liga_id = %s, sezona_id = %s,
                    domacin_id = %s, gost_id = %s,
                    kolo = %s, datum = %s,
                    ht_domacin_golovi = %s, ht_gost_golovi = %s,
                    ft_domacin_golovi = %s, ft_gost_golovi = %s
                WHERE id = %s
                RETURNING {MEC_KOLONE}
            """, (
                podaci.liga_id, podaci.sezona_id,
                podaci.domacin_id, podaci.gost_id,
                podaci.kolo, podaci.datum,
                podaci.ht_domacin_golovi, podaci.ht_gost_golovi,
                podaci.ft_domacin_golovi, podaci.ft_gost_golovi,
                mec_id,
            ))
            izmenjeni_mec = cur.fetchone()
        conn.commit()

    except (UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException) as greska:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(greska))

    if izmenjeni_mec is None:
        raise HTTPException(status_code=404, detail=f"Mec sa id={mec_id} ne postoji")
    return izmenjeni_mec


@app.delete("/mecevi/{mec_id}", status_code=204)
def obrisi_mec(mec_id: int, conn: DbConnection):
    """DELETE - brise mec. status_code=204 = "No Content", standardni
    kod kad je operacija uspela ali nema sta da se vrati u odgovoru."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.mec WHERE id = %s", (mec_id,))
        obrisano = cur.rowcount  # broj obrisanih redova (0 ili 1)
    conn.commit()

    if obrisano == 0:
        raise HTTPException(status_code=404, detail=f"Mec sa id={mec_id} ne postoji")
    # Kad se vrati 204, FastAPI ocekuje da funkcija ne vrati "telo"
    # odgovora - zato nema return vrednosti ovde.


# =====================================================================
# Izvestaj za jedan tim (tabela sa "zvezdicama")
# =====================================================================
# Ovo je SQL upit koji smo vec testirali direktno u psql-u
# (sql/03_izvestaj_tim.sql), sad prebacen u Python string. Umesto
# psql promenljivih (:'tim_id') koristimo psycopg placeholder-e
# u obliku %(ime)s, koje popunjavamo preko RECNIKA (dict) - ovo je
# zgodno kad se ista vrednost (npr. tim_id) pojavljuje vise puta u
# istom upitu, jer je pises samo jednom u recniku, a koristis
# vise puta u SQL-u.

UPIT_IZVESTAJ_TIM = """
WITH mecevi_tima AS (
    SELECT
        m.id,
        m.kolo,
        m.datum,
        d.naziv AS domacin,
        g.naziv AS gost,
        m.ht_domacin_golovi,
        m.ht_gost_golovi,
        m.ft_domacin_golovi,
        m.ft_gost_golovi,
        (m.domacin_id = %(tim_id)s) AS tim_je_domacin,
        CASE
            WHEN m.domacin_id = %(tim_id)s AND m.ft_domacin_golovi > m.ft_gost_golovi THEN 3
            WHEN m.gost_id    = %(tim_id)s AND m.ft_gost_golovi > m.ft_domacin_golovi THEN 3
            WHEN m.ft_domacin_golovi = m.ft_gost_golovi THEN 1
            ELSE 0
        END AS bodovi_meca,
        CASE WHEN m.domacin_id = %(tim_id)s
             THEN m.ft_domacin_golovi ELSE m.ft_gost_golovi END AS dati_golovi,
        CASE WHEN m.domacin_id = %(tim_id)s
             THEN m.ft_gost_golovi ELSE m.ft_domacin_golovi END AS primljeni_golovi
    FROM public.mec AS m
    JOIN public.tim AS d ON d.id = m.domacin_id
    JOIN public.tim AS g ON g.id = m.gost_id
    WHERE (m.domacin_id = %(tim_id)s OR m.gost_id = %(tim_id)s)
      AND m.sezona_id = %(sezona_id)s
),
sa_kumulativom AS (
    SELECT
        *,
        SUM(bodovi_meca)      OVER (ORDER BY kolo) AS bb,
        SUM(dati_golovi)      OVER (ORDER BY kolo) AS ukupno_dati,
        SUM(primljeni_golovi) OVER (ORDER BY kolo) AS ukupno_primljeni,
        CASE
            WHEN ht_domacin_golovi > ht_gost_golovi THEN '1'
            WHEN ht_domacin_golovi = ht_gost_golovi THEN 'X'
            ELSE '2'
        END AS ht_ishod,
        CASE
            WHEN ft_domacin_golovi > ft_gost_golovi THEN '1'
            WHEN ft_domacin_golovi = ft_gost_golovi THEN 'X'
            ELSE '2'
        END AS ft_ishod
    FROM mecevi_tima
)
SELECT
    kolo,
    domacin,
    gost,
    ht_domacin_golovi || ':' || ht_gost_golovi AS poluvreme,
    ft_domacin_golovi || ':' || ft_gost_golovi AS kraj,
    (ft_domacin_golovi > ft_gost_golovi) AS "1",
    (ft_domacin_golovi = ft_gost_golovi) AS "X",
    (ft_domacin_golovi < ft_gost_golovi) AS "2",
    (ht_domacin_golovi > ht_gost_golovi) AS "P1",
    (ht_domacin_golovi = ht_gost_golovi) AS "PX",
    (ht_domacin_golovi < ht_gost_golovi) AS "P2",
    (ht_domacin_golovi + ht_gost_golovi >= 1) AS "1+",
    (ht_domacin_golovi + ht_gost_golovi >= 2) AS "2+",
    (ht_domacin_golovi + ht_gost_golovi >= 3) AS "3+",
    (ft_domacin_golovi + ft_gost_golovi <= 1) AS "0-1",
    (ft_domacin_golovi + ft_gost_golovi <= 2) AS "0-2",
    (ft_domacin_golovi + ft_gost_golovi IN (2, 3)) AS "2-3",
    (ft_domacin_golovi + ft_gost_golovi >= 3) AS "3+_ukupno",
    (ft_domacin_golovi + ft_gost_golovi >= 4) AS "4+",
    (ft_domacin_golovi + ft_gost_golovi BETWEEN 4 AND 6) AS "4-6",
    (ft_domacin_golovi + ft_gost_golovi >= 5) AS "5+",
    (ft_domacin_golovi + ft_gost_golovi >= 7) AS "7+",
    (ht_ishod = '1' AND ft_ishod = '1') AS "1-1",
    (ht_ishod = '1' AND ft_ishod = 'X') AS "1-X",
    (ht_ishod = '1' AND ft_ishod = '2') AS "1-2",
    (ht_ishod = 'X' AND ft_ishod = '1') AS "X-1",
    (ht_ishod = 'X' AND ft_ishod = 'X') AS "X-X",
    (ht_ishod = 'X' AND ft_ishod = '2') AS "X-2",
    (ht_ishod = '2' AND ft_ishod = '1') AS "2-1",
    (ht_ishod = '2' AND ft_ishod = 'X') AS "2-X",
    (ht_ishod = '2' AND ft_ishod = '2') AS "2-2",
    (
        (ht_domacin_golovi + ht_gost_golovi)
        >
        ((ft_domacin_golovi - ht_domacin_golovi) + (ft_gost_golovi - ht_gost_golovi))
    ) AS "I>",
    (
        ((ft_domacin_golovi - ht_domacin_golovi) + (ft_gost_golovi - ht_gost_golovi))
        >
        (ht_domacin_golovi + ht_gost_golovi)
    ) AS "II>",
    (
        (ht_domacin_golovi + ht_gost_golovi)
        =
        ((ft_domacin_golovi - ht_domacin_golovi) + (ft_gost_golovi - ht_gost_golovi))
    ) AS "X_golovi",
    bb AS "BB",
    ukupno_dati || ':' || ukupno_primljeni AS "GR"
FROM sa_kumulativom
ORDER BY kolo;
"""


@app.get("/izvestaj/tim/{tim_id}")
def izvestaj_za_tim(tim_id: int, sezona_id: int, conn: DbConnection):
    """
    Vraca tabelu sa "zvezdicama" za jedan tim u jednoj sezoni.

    tim_id se uzima iz putanje URL-a (npr. /izvestaj/tim/1), a
    sezona_id je tzv. "query parametar" - FastAPI ga automatski
    prepoznaje jer se ne pojavljuje u putanji ({tim_id}), pa se poziva
    npr. ovako: /izvestaj/tim/1?sezona_id=1

    Ne koristimo Pydantic response_model ovde (za razliku od /mecevi)
    jer kolone imaju imena koja nisu validna kao Python promenljive
    ("1+", "0-1", "X-2"...) - vracamo "sirov" recnik po redu, koji
    FastAPI sam pretvara u JSON.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(UPIT_IZVESTAJ_TIM, {"tim_id": tim_id, "sezona_id": sezona_id})
        redovi = cur.fetchall()

    return {"tim_id": tim_id, "sezona_id": sezona_id, "mecevi": redovi}


# =====================================================================
# Izvestaj za ligu (sumarni brojevi po kolu ili opsegu kola)
# =====================================================================

UPIT_IZVESTAJ_LIGA = """
SELECT
    COUNT(*) AS ukupno_meceva,
    COUNT(*) FILTER (WHERE ft_domacin_golovi > ft_gost_golovi) AS "1",
    COUNT(*) FILTER (WHERE ft_domacin_golovi = ft_gost_golovi) AS "X",
    COUNT(*) FILTER (WHERE ft_domacin_golovi < ft_gost_golovi) AS "2",
    COUNT(*) FILTER (WHERE ht_domacin_golovi > ht_gost_golovi) AS "P1",
    COUNT(*) FILTER (WHERE ht_domacin_golovi = ht_gost_golovi) AS "PX",
    COUNT(*) FILTER (WHERE ht_domacin_golovi < ht_gost_golovi) AS "P2",
    COUNT(*) FILTER (WHERE ht_domacin_golovi + ht_gost_golovi >= 1) AS "1+",
    COUNT(*) FILTER (WHERE ht_domacin_golovi + ht_gost_golovi >= 2) AS "2+",
    COUNT(*) FILTER (WHERE ht_domacin_golovi + ht_gost_golovi >= 3) AS "3+",
    COUNT(*) FILTER (WHERE ft_domacin_golovi + ft_gost_golovi <= 1) AS "0-1",
    COUNT(*) FILTER (WHERE ft_domacin_golovi + ft_gost_golovi <= 2) AS "0-2",
    COUNT(*) FILTER (WHERE ft_domacin_golovi + ft_gost_golovi IN (2, 3)) AS "2-3",
    COUNT(*) FILTER (WHERE ft_domacin_golovi + ft_gost_golovi >= 3) AS "3+_ukupno",
    COUNT(*) FILTER (WHERE ft_domacin_golovi + ft_gost_golovi >= 4) AS "4+",
    COUNT(*) FILTER (WHERE ft_domacin_golovi + ft_gost_golovi BETWEEN 4 AND 6) AS "4-6",
    COUNT(*) FILTER (WHERE ft_domacin_golovi + ft_gost_golovi >= 5) AS "5+",
    COUNT(*) FILTER (WHERE ft_domacin_golovi + ft_gost_golovi >= 7) AS "7+",
    COUNT(*) FILTER (
        WHERE (ht_domacin_golovi + ht_gost_golovi)
            > ((ft_domacin_golovi - ht_domacin_golovi) + (ft_gost_golovi - ht_gost_golovi))
    ) AS "I>",
    COUNT(*) FILTER (
        WHERE ((ft_domacin_golovi - ht_domacin_golovi) + (ft_gost_golovi - ht_gost_golovi))
            > (ht_domacin_golovi + ht_gost_golovi)
    ) AS "II>",
    COUNT(*) FILTER (
        WHERE (ht_domacin_golovi + ht_gost_golovi)
            = ((ft_domacin_golovi - ht_domacin_golovi) + (ft_gost_golovi - ht_gost_golovi))
    ) AS "X_golovi"
FROM public.mec
WHERE liga_id = %(liga_id)s
  AND sezona_id = %(sezona_id)s
  -- COALESCE(%(kolo_od)s, kolo) znaci: "ako kolo_od NIJE poslat
  -- (NULL), koristi vrednost same kolone 'kolo' kao donju granicu"
  -- - sto je uvek tacno (kolo >= kolo), pa filter efektivno "nestaje"
  -- i ne ogranicava nista. Ovaj trik nam omogucava da kolo_od/kolo_do
  -- budu OPCIONI, bez potrebe da pisemo dve razlicite verzije upita.
  AND kolo BETWEEN COALESCE(%(kolo_od)s, kolo) AND COALESCE(%(kolo_do)s, kolo);
"""


@app.get("/izvestaj/liga/{liga_id}")
def izvestaj_za_ligu(
    liga_id: int,
    sezona_id: int,
    conn: DbConnection,
    kolo_od: int | None = None,
    kolo_do: int | None = None,
):
    """
    Sumarni izvestaj za ligu - broj mec eva po kategoriji (bez
    zvezdica, bez BB/GR, bez prelaza - dogovoreno ranije).

    Primeri poziva:
      /izvestaj/liga/1?sezona_id=1                    -> sva odigrana kola
      /izvestaj/liga/1?sezona_id=1&kolo_od=5&kolo_do=5 -> samo kolo 5
      /izvestaj/liga/1?sezona_id=1&kolo_od=1&kolo_do=7 -> kola 1 do 7
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(UPIT_IZVESTAJ_LIGA, {
            "liga_id": liga_id,
            "sezona_id": sezona_id,
            "kolo_od": kolo_od,
            "kolo_do": kolo_do,
        })
        rezultat = cur.fetchone()

    return {
        "liga_id": liga_id,
        "sezona_id": sezona_id,
        "kolo_od": kolo_od,
        "kolo_do": kolo_do,
        "statistika": rezultat,
    }


# =====================================================================
# Klasicna tabela lige (poredak timova po bodovima)
# =====================================================================

UPIT_TABELA_LIGE = """
WITH svi_nastupi AS (
    SELECT
        domacin_id AS tim_id,
        ft_domacin_golovi AS dati_golovi,
        ft_gost_golovi    AS primljeni_golovi,
        CASE
            WHEN ft_domacin_golovi > ft_gost_golovi THEN 3
            WHEN ft_domacin_golovi = ft_gost_golovi THEN 1
            ELSE 0
        END AS bodovi,
        (ft_domacin_golovi > ft_gost_golovi)::int AS pobeda,
        (ft_domacin_golovi = ft_gost_golovi)::int AS neresen,
        (ft_domacin_golovi < ft_gost_golovi)::int AS poraz
    FROM public.mec
    WHERE liga_id = %(liga_id)s AND sezona_id = %(sezona_id)s

    UNION ALL

    SELECT
        gost_id AS tim_id,
        ft_gost_golovi    AS dati_golovi,
        ft_domacin_golovi AS primljeni_golovi,
        CASE
            WHEN ft_gost_golovi > ft_domacin_golovi THEN 3
            WHEN ft_gost_golovi = ft_domacin_golovi THEN 1
            ELSE 0
        END AS bodovi,
        (ft_gost_golovi > ft_domacin_golovi)::int AS pobeda,
        (ft_gost_golovi = ft_domacin_golovi)::int AS neresen,
        (ft_gost_golovi < ft_domacin_golovi)::int AS poraz
    FROM public.mec
    WHERE liga_id = %(liga_id)s AND sezona_id = %(sezona_id)s
)
SELECT
    ROW_NUMBER() OVER (
        ORDER BY SUM(sn.bodovi) DESC,
                 (SUM(sn.dati_golovi) - SUM(sn.primljeni_golovi)) DESC,
                 SUM(sn.dati_golovi) DESC
    ) AS pozicija,
    t.naziv AS tim,
    COUNT(*) AS odigrano,
    SUM(sn.pobeda)  AS pobede,
    SUM(sn.neresen) AS neresene,
    SUM(sn.poraz)   AS porazi,
    SUM(sn.dati_golovi)      AS dati,
    SUM(sn.primljeni_golovi) AS primljeni,
    SUM(sn.dati_golovi) - SUM(sn.primljeni_golovi) AS gol_razlika,
    SUM(sn.bodovi) AS bodovi
FROM svi_nastupi AS sn
JOIN public.tim AS t ON t.id = sn.tim_id
GROUP BY t.id, t.naziv
ORDER BY pozicija;
"""


@app.get("/tabela-lige/{liga_id}")
def tabela_lige(liga_id: int, sezona_id: int, conn: DbConnection):
    """
    Klasican poredak timova u ligi za izabranu sezonu.

    Primer poziva: /tabela-lige/1?sezona_id=1
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(UPIT_TABELA_LIGE, {"liga_id": liga_id, "sezona_id": sezona_id})
        redovi = cur.fetchall()

    return {"liga_id": liga_id, "sezona_id": sezona_id, "tabela": redovi}


# =====================================================================
# "View" rute - vracaju HTML stranice (frontend), ne JSON
# =====================================================================
# Za razliku od svih ruta do sad (koje vracaju recnik/listu i FastAPI
# ga automatski pretvara u JSON), ove rute vracaju
# templates.TemplateResponse(...) - HTML stranicu generisanu iz
# .html fajla u app/templates/, popunjenu podacima.
#
# request: Request -> Jinja2Templates ZAHTEVA da svaka funkcija koja
# vraca HTML prima ceo "request" objekat (deo unutrasnjeg
# funkcionisanja FastAPI/Starlette-a - samo zapamti da ide, ne mora
# detaljnije objasnjenje za sada).

@app.get("/")
def pocetna_stranica(request: Request):
    """Pocetna stranica - samo navigacija, bez podataka iz baze."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/prikaz/tabela-lige")
def prikaz_tabele_lige(
    request: Request,
    conn: DbConnection,
    liga_id: int | None = None,
    sezona_id: int | None = None,
):
    """
    HTML stranica za tabelu lige. Kad se prvi put otvori (bez
    liga_id/sezona_id), prikazuje samo formu. Kad korisnik popuni
    formu i posalje je, ista ruta se ponovo pozove, ovog puta SA
    liga_id/sezona_id (jer forma salje GET zahtev sa tim vrednostima
    kao query parametrima), i mi tada upitujemo bazu i popunjavamo
    tabelu.
    """
    tabela = None
    greska = None

    if liga_id is not None and sezona_id is not None:
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(UPIT_TABELA_LIGE, {"liga_id": liga_id, "sezona_id": sezona_id})
                tabela = cur.fetchall()
        except Exception as e:
            greska = f"Greska pri ucitavanju tabele: {e}"

    return templates.TemplateResponse("tabela_lige.html", {
        "request": request,
        "liga_id": liga_id,
        "sezona_id": sezona_id,
        "tabela": tabela,
        "greska": greska,
    })


# Grupise kolone izvestaja lige u logicke sekcije za prikaz - lista
# torki (naziv_sekcije, [(labela_za_prikaz, kljuc_u_recniku), ...]).
# Ovo drzimo odvojeno od SQL-a jer je cisto "prezentacijski" detalj -
# SQL ne zna niti treba da zna kako se rezultat vizuelno grupise.
SEKCIJE_IZVESTAJ_LIGA = [
    ("Konacan rezultat", [("1", "1"), ("X", "X"), ("2", "2")]),
    ("Prvo poluvreme", [("P1", "P1"), ("PX", "PX"), ("P2", "P2")]),
    ("Golovi prvo poluvreme", [("1+", "1+"), ("2+", "2+"), ("3+", "3+")]),
    ("Golovi ukupno", [
        ("0-1", "0-1"), ("0-2", "0-2"), ("2-3", "2-3"), ("3+", "3+_ukupno"),
        ("4+", "4+"), ("4-6", "4-6"), ("5+", "5+"), ("7+", "7+"),
    ]),
    ("Pada vise golova", [("I>", "I>"), ("II>", "II>"), ("X", "X_golovi")]),
]


@app.get("/prikaz/izvestaj-liga")
def prikaz_izvestaja_lige(
    request: Request,
    conn: DbConnection,
    liga_id: int | None = None,
    sezona_id: int | None = None,
    # str | None umesto int | None: HTML forma salje prazan string ""
    # kad polje ostane nepopunjeno (ne izostavlja parametar potpuno),
    # a FastAPI ne moze "" da protumaci kao int - dobili bismo gresku
    # 422 pre nego sto nasa funkcija uopste bude pozvana. Zato ovde
    # prihvatamo sirov string i RUCNO ga pretvaramo u broj ispod,
    # gde imamo kontrolu da prazan string tretiramo kao "nema filtera".
    kolo_od: str | None = None,
    kolo_do: str | None = None,
):
    """HTML stranica za sumarni izvestaj lige - isti princip kao
    tabela lige (forma pa uslovno ucitavanje)."""
    statistika = None
    greska = None

    # int(x) if x else None -> ako je x prazan string (ili None),
    # rezultat je None; inace pretvaramo u ceo broj.
    kolo_od_broj = int(kolo_od) if kolo_od else None
    kolo_do_broj = int(kolo_do) if kolo_do else None

    if liga_id is not None and sezona_id is not None:
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(UPIT_IZVESTAJ_LIGA, {
                    "liga_id": liga_id,
                    "sezona_id": sezona_id,
                    "kolo_od": kolo_od_broj,
                    "kolo_do": kolo_do_broj,
                })
                statistika = cur.fetchone()
        except Exception as e:
            greska = f"Greska pri ucitavanju izvestaja: {e}"

    return templates.TemplateResponse("izvestaj_liga.html", {
        "request": request,
        "liga_id": liga_id,
        "sezona_id": sezona_id,
        "kolo_od": kolo_od_broj,
        "kolo_do": kolo_do_broj,
        "statistika": statistika,
        "sekcije": SEKCIJE_IZVESTAJ_LIGA,
        "greska": greska,
    })


# Isti princip kao SEKCIJE_IZVESTAJ_LIGA, ali za timski izvestaj -
# grupise "zvezdicaste" kolone po sekcijama radi dvorednog zaglavlja
# u tabeli (grupni naslov + pojedinacne oznake ispod).
SEKCIJE_IZVESTAJ_TIM = [
    ("Konacan rezultat", [("1", "1"), ("X", "X"), ("2", "2")]),
    ("Prvo poluvreme", [("P1", "P1"), ("PX", "PX"), ("P2", "P2")]),
    ("Golovi prvo poluvreme", [("1+", "1+"), ("2+", "2+"), ("3+", "3+")]),
    ("Golovi ukupno", [
        ("0-1", "0-1"), ("0-2", "0-2"), ("2-3", "2-3"), ("3+", "3+_ukupno"),
        ("4+", "4+"), ("4-6", "4-6"), ("5+", "5+"), ("7+", "7+"),
    ]),
    ("Prelaz (poluvreme - kraj)", [
        ("1-1", "1-1"), ("1-X", "1-X"), ("1-2", "1-2"),
        ("X-1", "X-1"), ("X-X", "X-X"), ("X-2", "X-2"),
        ("2-1", "2-1"), ("2-X", "2-X"), ("2-2", "2-2"),
    ]),
    ("Pada vise golova", [("I>", "I>"), ("II>", "II>"), ("X", "X_golovi")]),
]


@app.get("/prikaz/izvestaj-tim")
def prikaz_izvestaja_tima(
    request: Request,
    conn: DbConnection,
    tim_id: int | None = None,
    sezona_id: int | None = None,
):
    """HTML stranica za izvestaj tima (tabela sa zvezdicama) - isti
    princip kao ostale prikaz-stranice."""
    mecevi = None
    greska = None

    if tim_id is not None and sezona_id is not None:
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(UPIT_IZVESTAJ_TIM, {"tim_id": tim_id, "sezona_id": sezona_id})
                mecevi = cur.fetchall()
        except Exception as e:
            greska = f"Greska pri ucitavanju izvestaja: {e}"

    return templates.TemplateResponse("izvestaj_tim.html", {
        "request": request,
        "tim_id": tim_id,
        "sezona_id": sezona_id,
        "mecevi": mecevi,
        "sekcije": SEKCIJE_IZVESTAJ_TIM,
        "greska": greska,
    })


# =====================================================================
# Forma za unos meca (dvokorakna: prvo liga+sezona, pa tim+rezultat)
# =====================================================================

UPIT_LIGE = "SELECT id, naziv, drzava FROM public.liga ORDER BY naziv"
UPIT_SEZONE = "SELECT id, naziv FROM public.sezona ORDER BY naziv"

UPIT_TIMOVI_U_LIGI = """
    SELECT t.id, t.naziv
    FROM public.tim_liga_sezona AS tls
    JOIN public.tim AS t ON t.id = tls.tim_id
    WHERE tls.liga_id = %(liga_id)s AND tls.sezona_id = %(sezona_id)s
    ORDER BY t.naziv
"""

# Poslednjih par unetih meceva za izabranu ligu/sezonu - prikazujemo
# ih na formi kao "brzu potvrdu" da je unos prosao, bez potrebe da
# korisnik odlazi na drugu stranicu da proveri.
UPIT_POSLEDNJI_MECEVI = """
    SELECT m.kolo, d.naziv AS domacin, g.naziv AS gost,
           m.ft_domacin_golovi, m.ft_gost_golovi
    FROM public.mec AS m
    JOIN public.tim AS d ON d.id = m.domacin_id
    JOIN public.tim AS g ON g.id = m.gost_id
    WHERE m.liga_id = %(liga_id)s AND m.sezona_id = %(sezona_id)s
    ORDER BY m.id DESC
    LIMIT 10
"""

UPIT_UNESI_MEC = f"""
    INSERT INTO public.mec (
        liga_id, sezona_id, domacin_id, gost_id, kolo, datum,
        ht_domacin_golovi, ht_gost_golovi,
        ft_domacin_golovi, ft_gost_golovi
    )
    VALUES (
        %(liga_id)s, %(sezona_id)s, %(domacin_id)s, %(gost_id)s, %(kolo)s, %(datum)s,
        %(ht_domacin_golovi)s, %(ht_gost_golovi)s, %(ft_domacin_golovi)s, %(ft_gost_golovi)s
    )
    RETURNING {MEC_KOLONE}
"""


def ucitaj_kontekst_forme(conn: Connection, liga_id: int | None, sezona_id: int | None):
    """
    Pomocna funkcija (obican Python helper, ne FastAPI ruta) koja
    prikuplja sve podatke potrebne formi: listu svih liga, listu svih
    sezona, i - ako su liga_id i sezona_id vec izabrani - listu
    timova prijavljenih u tu ligu/sezonu i poslednje unete meceve.

    Izdvojili smo ovo u posebnu funkciju jer je isti "paket podataka"
    potreban i GET ruti (prikaz forme) i POST ruti (ponovni prikaz
    forme nakon unosa, sa porukom o uspehu/gresci) - bez ovoga,
    morali bismo da kopiramo isti kod na dva mesta.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(UPIT_LIGE)
        lige = cur.fetchall()

        cur.execute(UPIT_SEZONE)
        sezone = cur.fetchall()

        timovi = []
        poslednji_mecevi = []
        if liga_id is not None and sezona_id is not None:
            cur.execute(UPIT_TIMOVI_U_LIGI, {"liga_id": liga_id, "sezona_id": sezona_id})
            timovi = cur.fetchall()

            cur.execute(UPIT_POSLEDNJI_MECEVI, {"liga_id": liga_id, "sezona_id": sezona_id})
            poslednji_mecevi = cur.fetchall()

    return {
        "lige": lige,
        "sezone": sezone,
        "timovi": timovi,
        "poslednji_mecevi": poslednji_mecevi,
    }


@app.get("/prikaz/unos-meca")
def prikaz_forme_unos(
    request: Request,
    conn: DbConnection,
    liga_id: int | None = None,
    sezona_id: int | None = None,
):
    """Prvi prikaz forme (ili nakon sto korisnik izabere ligu/sezonu
    iz prvog dela forme, sto salje GET zahtev nazad na istu rutu)."""
    kontekst = ucitaj_kontekst_forme(conn, liga_id, sezona_id)
    return templates.TemplateResponse("unos_meca.html", {
        "request": request,
        "liga_id": liga_id,
        "sezona_id": sezona_id,
        "greska": None,
        "poruka_uspeh": None,
        **kontekst,
    })


@app.post("/prikaz/unos-meca")
def obradi_unos_meca(
    request: Request,
    conn: DbConnection,
    # Form(...) govori FastAPI-ju da ova vrednost NE dolazi iz JSON
    # tela (kao kod /mecevi API rute) niti iz URL putanje/query stringa,
    # vec iz podataka koje browser salje kad korisnik posalje HTML
    # <form> (Content-Type: application/x-www-form-urlencoded).
    liga_id: Annotated[int, Form()],
    sezona_id: Annotated[int, Form()],
    domacin_id: Annotated[int, Form()],
    gost_id: Annotated[int, Form()],
    kolo: Annotated[int, Form()],
    datum: Annotated[str, Form()] = "",
    ht_domacin_golovi: Annotated[int, Form()] = 0,
    ht_gost_golovi: Annotated[int, Form()] = 0,
    ft_domacin_golovi: Annotated[int, Form()] = 0,
    ft_gost_golovi: Annotated[int, Form()] = 0,
):
    """
    Obradjuje posalju formu za unos meca. Za razliku od POST /mecevi
    (nasa "API" ruta), ovde rucno pravimo MecCreate objekat od
    Form() vrednosti i rucno hvatamo ValidationError - jer forma ne
    salje JSON, pa FastAPI ne moze automatski da je proveri protiv
    MecCreate modela kao kod API rute.
    """
    greska = None
    poruka_uspeh = None

    try:
        podaci = MecCreate(
            liga_id=liga_id, sezona_id=sezona_id,
            domacin_id=domacin_id, gost_id=gost_id,
            kolo=kolo, datum=datum or None,
            ht_domacin_golovi=ht_domacin_golovi, ht_gost_golovi=ht_gost_golovi,
            ft_domacin_golovi=ft_domacin_golovi, ft_gost_golovi=ft_gost_golovi,
        )
    except ValidationError as e:
        # e.errors()[0]["msg"] uzima poruku PRVE greske (ako ih ima
        # vise) - dovoljno za prikaz jedne jasne poruke korisniku.
        greska = e.errors()[0]["msg"]
        podaci = None

    if podaci is not None:
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(UPIT_UNESI_MEC, podaci.model_dump())
                novi_mec = cur.fetchone()
            conn.commit()
            poruka_uspeh = (
                f"Mec uspesno unet: {novi_mec['kolo']}. kolo, "
                f"rezultat {novi_mec['ft_domacin_golovi']}:{novi_mec['ft_gost_golovi']}"
            )
        except (UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException) as e:
            conn.rollback()
            greska = str(e)

    kontekst = ucitaj_kontekst_forme(conn, liga_id, sezona_id)
    return templates.TemplateResponse("unos_meca.html", {
        "request": request,
        "liga_id": liga_id,
        "sezona_id": sezona_id,
        "greska": greska,
        "poruka_uspeh": poruka_uspeh,
        **kontekst,
    })


# =====================================================================
# Admin stranica - dodavanje liga, sezona, timova, i njihovo
# povezivanje (tim_liga_sezona) - sve kroz web forme, bez rucnog SQL-a
# =====================================================================

UPIT_DODAJ_LIGU = """
    INSERT INTO public.liga (naziv, drzava, nivo_takmicenja)
    VALUES (%(naziv)s, %(drzava)s, %(nivo_takmicenja)s)
    RETURNING id, naziv
"""
UPIT_DODAJ_SEZONU = """
    INSERT INTO public.sezona (naziv)
    VALUES (%(naziv)s)
    RETURNING id, naziv
"""
UPIT_DODAJ_TIM = """
    INSERT INTO public.tim (naziv, grad)
    VALUES (%(naziv)s, %(grad)s)
    RETURNING id, naziv
"""
UPIT_POVEZI_TIM = """
    INSERT INTO public.tim_liga_sezona (tim_id, liga_id, sezona_id)
    VALUES (%(tim_id)s, %(liga_id)s, %(sezona_id)s)
    RETURNING id
"""
UPIT_SVI_TIMOVI = "SELECT id, naziv, grad FROM public.tim ORDER BY naziv"
UPIT_SVE_VEZE = """
    SELECT tls.id, t.naziv AS tim, l.naziv AS liga, s.naziv AS sezona
    FROM public.tim_liga_sezona AS tls
    JOIN public.tim    AS t ON t.id = tls.tim_id
    JOIN public.liga   AS l ON l.id = tls.liga_id
    JOIN public.sezona AS s ON s.id = tls.sezona_id
    ORDER BY s.naziv DESC, l.naziv, t.naziv
"""


def ucitaj_kontekst_admin(conn: Connection):
    """Prikuplja sve liste potrebne admin stranici (lige, sezone,
    timovi, sve postojece veze) - isti princip kao
    ucitaj_kontekst_forme, da ne ponavljamo kod na vise mesta."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(UPIT_LIGE)
        lige = cur.fetchall()
        cur.execute(UPIT_SEZONE)
        sezone = cur.fetchall()
        cur.execute(UPIT_SVI_TIMOVI)
        timovi = cur.fetchall()
        cur.execute(UPIT_SVE_VEZE)
        veze = cur.fetchall()
    return {"lige": lige, "sezone": sezone, "timovi": timovi, "veze": veze}


@app.get("/prikaz/admin")
def prikaz_admin(request: Request, conn: DbConnection, poruka: str | None = None, greska: str | None = None):
    """
    Glavna admin stranica. poruka/greska stizu kao query parametri -
    to je deo POST-Redirect-GET obrasca (objasnjeno ispod, u
    dodaj_ligu i ostalim POST rutama): nakon uspesnog (ili neuspesnog)
    unosa, ne prikazujemo stranicu direktno iz POST rute, vec
    RADIMO REDIRECT nazad na ovu GET rutu, sa porukom "provucenom"
    kroz URL (?poruka=... ili ?greska=...). Zasto: ako bismo direktno
    vratili HTML iz POST rute, i korisnik pritisne F5 (osvezi
    stranicu), browser bi PONOVO poslao isti POST zahtev - rizik da
    se isti unos slucajno ponovi. Redirect na GET to sprecava, jer
    F5 posle redirecta samo ponovo ucitava GET (bezopasno).
    """
    kontekst = ucitaj_kontekst_admin(conn)
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "poruka": poruka,
        "greska": greska,
        **kontekst,
    })


@app.post("/prikaz/admin/liga")
def dodaj_ligu(conn: DbConnection, naziv: Annotated[str, Form()], drzava: Annotated[str, Form()] = "", nivo_takmicenja: Annotated[str, Form()] = ""):
    try:
        podaci = LigaCreate(naziv=naziv, drzava=drzava or None, nivo_takmicenja=nivo_takmicenja or None)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(UPIT_DODAJ_LIGU, podaci.model_dump())
            nova = cur.fetchone()
        conn.commit()
        # quote(...) "escapuje" specijalne karaktere (razmake, slova
        # sa kvakicama itd.) da bi tekst bezbedno mogao da se stavi u
        # URL - bez ovoga, poruka sa razmacima bi pokvarila URL.
        return RedirectResponse(f"/prikaz/admin?poruka={quote(f'Liga dodata: {nova["naziv"]}')}#sekcija-liga", status_code=303)
    except ValidationError as e:
        return RedirectResponse(f"/prikaz/admin?greska={quote(e.errors()[0]['msg'])}#sekcija-liga", status_code=303)
    except (UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException) as e:
        conn.rollback()
        return RedirectResponse(f"/prikaz/admin?greska={quote(str(e))}#sekcija-liga", status_code=303)


@app.post("/prikaz/admin/sezona")
def dodaj_sezonu(conn: DbConnection, naziv: Annotated[str, Form()]):
    try:
        podaci = SezonaCreate(naziv=naziv)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(UPIT_DODAJ_SEZONU, podaci.model_dump())
            nova = cur.fetchone()
        conn.commit()
        return RedirectResponse(f"/prikaz/admin?poruka={quote(f'Sezona dodata: {nova["naziv"]}')}#sekcija-sezona", status_code=303)
    except ValidationError as e:
        return RedirectResponse(f"/prikaz/admin?greska={quote(e.errors()[0]['msg'])}#sekcija-sezona", status_code=303)
    except (UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException) as e:
        conn.rollback()
        return RedirectResponse(f"/prikaz/admin?greska={quote(str(e))}#sekcija-sezona", status_code=303)


@app.post("/prikaz/admin/tim")
def dodaj_tim(conn: DbConnection, naziv: Annotated[str, Form()], grad: Annotated[str, Form()] = ""):
    try:
        podaci = TimCreate(naziv=naziv, grad=grad or None)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(UPIT_DODAJ_TIM, podaci.model_dump())
            novi = cur.fetchone()
        conn.commit()
        return RedirectResponse(f"/prikaz/admin?poruka={quote(f'Tim dodat: {novi["naziv"]}')}#sekcija-tim", status_code=303)
    except ValidationError as e:
        return RedirectResponse(f"/prikaz/admin?greska={quote(e.errors()[0]['msg'])}#sekcija-tim", status_code=303)
    except (UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException) as e:
        conn.rollback()
        return RedirectResponse(f"/prikaz/admin?greska={quote(str(e))}#sekcija-tim", status_code=303)


@app.post("/prikaz/admin/veza")
def dodaj_vezu(
    conn: DbConnection,
    tim_id: Annotated[int, Form()],
    liga_id: Annotated[int, Form()],
    sezona_id: Annotated[int, Form()],
):
    """Povezuje tim sa ligom/sezonom (upisuje u tim_liga_sezona) -
    ovo zamenjuje rucni SQL INSERT koji smo do sad koristili."""
    try:
        podaci = TimLigaSezonaCreate(tim_id=tim_id, liga_id=liga_id, sezona_id=sezona_id)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(UPIT_POVEZI_TIM, podaci.model_dump())
        conn.commit()
        return RedirectResponse(f"/prikaz/admin?poruka={quote('Tim uspesno povezan sa ligom/sezonom')}#sekcija-veza", status_code=303)
    except (UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException) as e:
        conn.rollback()
        return RedirectResponse(f"/prikaz/admin?greska={quote(str(e))}#sekcija-veza", status_code=303)
