#!/usr/bin/env python3
"""
uvoz_api_football.py
=====================================================================
Samostalan skript koji povlaci meceve sa api-football.com i uvozi ih
u doktorova_statistika bazu. Odvojen je od glavne FastAPI aplikacije
(main.py) - pokrece se rucno iz terminala, ne kao deo web servera.

KAKO SE KORISTI:
  python3 uvoz_api_football.py \
      --liga-id 6 --sezona-id 1 \
      --api-liga-id 78 --api-sezona 2026

  --liga-id      = ID lige u NASOJ bazi (public.liga.id)
  --sezona-id    = ID sezone u NASOJ bazi (public.sezona.id)
  --api-liga-id  = ID lige u api-football.com sistemu (78 = Bundesliga)
  --api-sezona   = godina sezone kako je API ocekuje (npr. 2026 za
                   sezonu 2026/27 - API uvek koristi POCETNU godinu)

PRETPOSTAVKA (kako si i sam naveo): liga, sezona, i SVI timovi te
lige/sezone (ukljucujuci vezu tim_liga_sezona) su VEC uneti rucno
kroz nasu admin stranicu, PRE pokretanja ovog skripta.
=====================================================================
"""

import argparse
import re
import sys
from datetime import datetime

import psycopg
import requests
from psycopg.errors import UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException
from psycopg.rows import dict_row

API_BASE_URL = "https://v3.football.api-sports.io"


def ucitaj_env(putanja=".env") -> dict:
    """
    Rucno cita .env fajl (isti format kao za FastAPI aplikaciju -
    KLJUC=vrednost, po jedan par u redu). Ne koristimo
    pydantic-settings ovde jer je ovo NEZAVISAN skript, ne deo
    FastAPI aplikacije - jednostavniji, "gol" pristup je dovoljan.
    """
    podaci = {}
    with open(putanja) as f:
        for linija in f:
            linija = linija.strip()
            if not linija or linija.startswith("#") or "=" not in linija:
                continue
            kljuc, vrednost = linija.split("=", 1)
            podaci[kljuc.strip()] = vrednost.strip()
    return podaci


def api_poziv(putanja: str, parametri: dict, api_kljuc: str) -> dict:
    """Salje GET zahtev ka api-football.com i vraca 'response' deo
    JSON odgovora (gde se nalaze stvarni podaci)."""
    odgovor = requests.get(
        f"{API_BASE_URL}{putanja}",
        headers={"x-apisports-key": api_kljuc},
        params=parametri,
        timeout=15,
    )
    odgovor.raise_for_status()  # baca gresku ako HTTP status nije 200
    podaci = odgovor.json()
    if podaci.get("errors"):
        print(f"  UPOZORENJE - API je vratio gresku: {podaci['errors']}")
    return podaci.get("response", [])


def parsiraj_kolo(round_tekst: str) -> int | None:
    """
    API vraca kolo kao tekst, npr. 'Regular Season - 5'. Trazimo
    POSLEDNJI broj u tom tekstu regex-om (\\d+ = jedna ili vise
    cifara, $ = na kraju stringa) i pretvaramo ga u int.
    """
    poklapanje = re.search(r"(\d+)\s*$", round_tekst)
    return int(poklapanje.group(1)) if poklapanje else None


def main():
    parser = argparse.ArgumentParser(description="Uvoz meceva sa api-football.com")
    parser.add_argument("--liga-id", type=int, required=True, help="ID lige u nasoj bazi")
    parser.add_argument("--sezona-id", type=int, required=True, help="ID sezone u nasoj bazi")
    parser.add_argument("--api-liga-id", type=int, required=True, help="ID lige na api-football.com")
    parser.add_argument("--api-sezona", type=int, required=True, help="Godina sezone (npr. 2026)")
    args = parser.parse_args()

    env = ucitaj_env()
    api_kljuc = env["API_FOOTBALL_KEY"]

    conn = psycopg.connect(
        host=env["DB_HOST"], port=env["DB_PORT"], dbname=env["DB_NAME"],
        user=env["DB_USER"], password=env["DB_PASSWORD"],
    )

    # -----------------------------------------------------------------
    # KORAK 1: provera pokrivenosti sezone (informativno, trosi 1 poziv)
    # -----------------------------------------------------------------
    print(f"Proveravam dostupnost lige {args.api_liga_id}, sezona {args.api_sezona}...")
    lige_info = api_poziv("/leagues", {"id": args.api_liga_id}, api_kljuc)
    if not lige_info:
        print("GRESKA: API nije vratio nikakve podatke za ovaj league id. Proveri da li je ispravan.")
        sys.exit(1)

    naziv_lige = lige_info[0]["league"]["name"]
    sezone = lige_info[0].get("seasons", [])
    trazena_sezona = next((s for s in sezone if s["year"] == args.api_sezona), None)

    print(f"Liga: {naziv_lige}")
    if trazena_sezona is None:
        print(f"UPOZORENJE: sezona {args.api_sezona} se ne pojavljuje u listi dostupnih sezona za ovu ligu.")
        print(f"Dostupne godine: {[s['year'] for s in sezone]}")
    else:
        print(f"Sezona {args.api_sezona} pronadjena. Pokrivenost mecevi/rezultati: "
              f"{trazena_sezona.get('coverage', {}).get('fixtures', {})}")

    nastavi = input("\nDa li da nastavim sa uvozom timova i meceva? (da/ne): ").strip().lower()
    if nastavi != "da":
        print("Prekinuto.")
        sys.exit(0)

    # -----------------------------------------------------------------
    # KORAK 2: mapiranje timova (API tim_id -> nas tim_id)
    # -----------------------------------------------------------------
    print("\nUcitavam listu timova sa API-ja...")
    api_timovi = api_poziv("/teams", {"league": args.api_liga_id, "season": args.api_sezona}, api_kljuc)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT t.id, t.naziv, t.api_football_id
            FROM public.tim AS t
            JOIN public.tim_liga_sezona AS tls ON tls.tim_id = t.id
            WHERE tls.liga_id = %(liga_id)s AND tls.sezona_id = %(sezona_id)s
        """, {"liga_id": args.liga_id, "sezona_id": args.sezona_id})
        nasi_timovi = cur.fetchall()

    nasi_po_imenu = {t["naziv"].lower(): t for t in nasi_timovi}
    mapa_api_id_na_nas_id = {}
    nepovezani = []

    for api_tim in api_timovi:
        api_naziv = api_tim["team"]["name"]
        api_id = api_tim["team"]["id"]
        nas_tim = nasi_po_imenu.get(api_naziv.lower())

        if nas_tim is None:
            nepovezani.append((api_naziv, api_id))
            continue

        mapa_api_id_na_nas_id[api_id] = nas_tim["id"]

        # Upisujemo api_football_id u nas tim (samo ako vec nije upisan)
        if nas_tim["api_football_id"] != api_id:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.tim SET api_football_id = %s WHERE id = %s",
                    (api_id, nas_tim["id"]),
                )
            conn.commit()

    print(f"Povezano timova: {len(mapa_api_id_na_nas_id)} od {len(api_timovi)}")
    if nepovezani:
        print("\nSLEDECI TIMOVI NISU PRONADJENI po imenu u nasoj bazi (proveri pisanje imena):")
        for naziv, api_id in nepovezani:
            print(f"  - '{naziv}' (api id: {api_id})")
        print("Mecevi koji ukljucuju ove timove ce biti PRESKOCENI.\n")

    # -----------------------------------------------------------------
    # KORAK 3: povlacenje odigranih meceva (samo zavrseni - status FT)
    # -----------------------------------------------------------------
    print("Ucitavam meceve sa API-ja...")
    fixtures = api_poziv(
        "/fixtures",
        {"league": args.api_liga_id, "season": args.api_sezona, "status": "FT"},
        api_kljuc,
    )
    print(f"Pronadjeno zavrsenih meceva: {len(fixtures)}")

    # -----------------------------------------------------------------
    # KORAK 4: uvoz - isti princip kao CSV uvoz (red po red, svaki u
    # svojoj mini-transakciji, greske se preskacu i prijavljuju)
    # -----------------------------------------------------------------
    upit_unesi_mec = """
        INSERT INTO public.mec (
            liga_id, sezona_id, domacin_id, gost_id, kolo, datum,
            ht_domacin_golovi, ht_gost_golovi, ft_domacin_golovi, ft_gost_golovi
        )
        VALUES (%(liga_id)s, %(sezona_id)s, %(domacin_id)s, %(gost_id)s, %(kolo)s, %(datum)s,
                %(ht_domacin_golovi)s, %(ht_gost_golovi)s, %(ft_domacin_golovi)s, %(ft_gost_golovi)s)
    """

    broj_uspesnih = 0
    preskoceni = []

    for utakmica in fixtures:
        api_domacin_id = utakmica["teams"]["home"]["id"]
        api_gost_id = utakmica["teams"]["away"]["id"]
        naziv_meca = f"{utakmica['teams']['home']['name']} - {utakmica['teams']['away']['name']}"

        domacin_id = mapa_api_id_na_nas_id.get(api_domacin_id)
        gost_id = mapa_api_id_na_nas_id.get(api_gost_id)
        if domacin_id is None or gost_id is None:
            preskoceni.append((naziv_meca, "tim nije povezan (vidi listu iznad)"))
            continue

        kolo = parsiraj_kolo(utakmica["league"]["round"])
        if kolo is None:
            preskoceni.append((naziv_meca, f"ne mogu da odredim kolo iz teksta: '{utakmica['league']['round']}'"))
            continue

        # fixture.date je ISO8601 sa vremenskom zonom, npr.
        # "2026-09-13T14:30:00+00:00" - uzimamo samo datumski deo (10 karaktera)
        datum = utakmica["fixture"]["date"][:10]

        ht = utakmica["score"]["halftime"]
        ft = utakmica["score"]["fulltime"]
        if ht["home"] is None or ft["home"] is None:
            preskoceni.append((naziv_meca, "nedostaje rezultat poluvremena ili kraja"))
            continue

        try:
            with conn.cursor() as cur:
                cur.execute(upit_unesi_mec, {
                    "liga_id": args.liga_id, "sezona_id": args.sezona_id,
                    "domacin_id": domacin_id, "gost_id": gost_id,
                    "kolo": kolo, "datum": datum,
                    "ht_domacin_golovi": ht["home"], "ht_gost_golovi": ht["away"],
                    "ft_domacin_golovi": ft["home"], "ft_gost_golovi": ft["away"],
                })
            conn.commit()
            broj_uspesnih += 1
        except (UniqueViolation, CheckViolation, ForeignKeyViolation, RaiseException) as e:
            conn.rollback()
            preskoceni.append((naziv_meca, str(e).split("\n")[0]))

    print(f"\n=== ZAVRSENO ===")
    print(f"Uspesno uneto: {broj_uspesnih}")
    print(f"Preskoceno: {len(preskoceni)}")
    for naziv, razlog in preskoceni:
        print(f"  - {naziv}: {razlog}")

    conn.close()


if __name__ == "__main__":
    main()
