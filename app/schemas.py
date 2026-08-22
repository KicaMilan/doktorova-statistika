# app/schemas.py
#
# "Schema" ovde ne znaci sema baze - to je Pydantic terminologija za
# klasu koja opisuje OBLIK podataka (koja polja postoje, kog su tipa,
# koja pravila moraju da vaze). FastAPI automatski:
#   1) proveri da li podaci koje je klijent poslao odgovaraju modelu
#   2) ako ne odgovaraju, vrati jasnu gresku (400) BEZ da uopste
#      pozovemo bazu
#   3) ako odgovaraju, konvertuje ih u Python objekat kojim mozemo
#      slobodno da rukujemo u kodu

from datetime import date

from pydantic import BaseModel, model_validator


class MecCreate(BaseModel):
    """
    Oblik podataka koje OCEKUJEMO kad neko zeli da UNESE novi mec.
    Ime "Create" je konvencija - kasnije cemo imati i MecUpdate,
    MecOut itd, svaki za drugu namenu.
    """
    liga_id: int
    sezona_id: int
    domacin_id: int
    gost_id: int
    kolo: int
    # date | None znaci "pravi datum ili nista" - ovo polje nije
    # obavezno. Pydantic sam prepoznaje ISO format ("2026-09-13")
    # kad stigne kao JSON string i pretvara ga u pravi Python 'date'
    # objekat - ne moramo mi rucno da parsiramo.
    datum: date | None = None

    ht_domacin_golovi: int
    ht_gost_golovi: int
    ft_domacin_golovi: int
    ft_gost_golovi: int

    # @model_validator(mode="after") pokrece se NAKON sto su sva
    # pojedinacna polja vec prosla osnovnu proveru tipa (da li je
    # kolo stvarno broj, itd). Ovde proveravamo pravila koja
    # zavise od VISE polja istovremeno - isto ono sto smo vec
    # osigurali CHECK constraint-ima u bazi, samo sad na Python strani,
    # da korisnik odmah dobije jasnu poruku umesto SQL greske.
    @model_validator(mode="after")
    def proveri_logicnost_rezultata(self):
        if self.domacin_id == self.gost_id:
            raise ValueError("Domacin i gost ne mogu biti isti tim")

        if self.kolo <= 0:
            raise ValueError("Kolo mora biti pozitivan broj")

        golovi = [
            self.ht_domacin_golovi, self.ht_gost_golovi,
            self.ft_domacin_golovi, self.ft_gost_golovi,
        ]
        if any(g < 0 for g in golovi):
            raise ValueError("Broj golova ne moze biti negativan")

        if self.ht_domacin_golovi > self.ft_domacin_golovi:
            raise ValueError(
                "Golovi domacina na poluvremenu ne mogu biti veci od golova na kraju meca"
            )
        if self.ht_gost_golovi > self.ft_gost_golovi:
            raise ValueError(
                "Golovi gosta na poluvremenu ne mogu biti veci od golova na kraju meca"
            )

        # Validator mora da vrati self - ako sve prodje, objekat
        # ostaje nepromenjen i FastAPI nastavlja dalje.
        return self


class MecUpdate(MecCreate):
    """
    Za izmenu meca ocekujemo ISTI oblik podataka kao za kreiranje
    (sva polja se ponovo salju, cak i ako se samo jedno menja - ovo
    se zove PUT pristup, jednostavniji za pocetak od "delimicnog"
    PATCH-a). Zato samo nasledjujemo MecCreate - ne moramo nista
    ponovo da pisemo.
    """
    pass


class MecOut(BaseModel):
    """
    Oblik podataka koje VRACAMO klijentu kad cita mec (uklju\u010duje id,
    za razliku od MecCreate gde id jos ne postoji jer ga baza tek
    generise).
    """
    id: int
    liga_id: int
    sezona_id: int
    domacin_id: int
    gost_id: int
    kolo: int
    datum: date | None
    ht_domacin_golovi: int
    ht_gost_golovi: int
    ft_domacin_golovi: int
    ft_gost_golovi: int


# =====================================================================
# Modeli za administrativni unos (liga, sezona, tim, njihova veza)
# =====================================================================
# Ovo su jednostavniji modeli - nemaju kompleksnu unakrsnu validaciju
# kao MecCreate, samo osnovna pravila (npr. da naziv nije prazan).

class LigaCreate(BaseModel):
    naziv: str
    drzava: str | None = None
    nivo_takmicenja: str | None = None

    @model_validator(mode="after")
    def proveri_naziv(self):
        if not self.naziv.strip():
            raise ValueError("Naziv lige ne sme biti prazan")
        return self


class SezonaCreate(BaseModel):
    naziv: str

    @model_validator(mode="after")
    def proveri_naziv(self):
        if not self.naziv.strip():
            raise ValueError("Naziv sezone ne sme biti prazan")
        return self


class TimCreate(BaseModel):
    naziv: str
    grad: str | None = None

    @model_validator(mode="after")
    def proveri_naziv(self):
        if not self.naziv.strip():
            raise ValueError("Naziv tima ne sme biti prazan")
        return self


class TimLigaSezonaCreate(BaseModel):
    tim_id: int
    liga_id: int
    sezona_id: int
