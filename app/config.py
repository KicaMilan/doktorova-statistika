# app/config.py
#
# Ovaj fajl ("modul" u Python terminologiji - svaki .py fajl je modul
# koji moze da se "importuje"/ucita u drugi fajl) je zaduzen SAMO za
# citanje konfiguracije. Ne pravi konekciju, ne pise SQL - samo cuva
# podatke o tome KAKO da se baza kontaktira.
#
# BaseSettings (iz pydantic-settings) automatski cita vrednosti iz
# .env fajla i "mapira" ih na Python promenljive ispod. Ovo je
# standardan nacin da se osetljivi podaci (lozinke) drze VAN koda.

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
        # Ime promenljive ovde (db_host) mora da odgovara imenu u .env
            # fajlu (DB_HOST) - pydantic ignorise velika/mala slova pri poredjenju.
                db_host: str
                db_port: int = 5432          # "= 5432" znaci podrazumevana vrednost
                db_name: str
                db_user: str
                db_password: str

                class Config:
                    # Ovde kazemo pydantic-u: "procitaj vrednosti iz fajla .env"
                    env_file = ".env"


# Kreiramo JEDNU instancu (objekat) klase Settings, koju ceo ostatak
# aplikacije uvozi i koristi. Ovo se zove "singleton" pattern -
# konfiguracija se cita samo jednom, pri pokretanju aplikacije.
settings = Settings()
