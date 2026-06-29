#!/usr/bin/env python3
"""
Generate scanner/statlige_organ.json — the seed list of Norwegian STATE bodies
(statlige organ: departementer, direktorater, etater, tilsyn) that the email
scanner classifies, the way kommuner_wikidata.json seeds the kommuner. Health
trusts and the UH sector get their own seeds (see gen_sectors.py).

The list of bodies is a CURATED selection of the big, recognisable central-state
organs (NAV, Skatteetaten, politiet, departementer, helseforetak, …) — central
state IT is even more Microsoft-consolidated than the kommuner, so these names
make the dependence concrete. Each body's LEGAL IDENTITY (official name +
website) is resolved live from the public Brønnøysund Enhetsregisteret by its
organisasjonsnummer — that is the citable source (CLAUDE.md rule 1 / rule 4):

    https://data.brreg.no/enhetsregisteret/api/enheter/<orgnr>

The MAIL DOMAIN is NOT in Enhetsregisteret (it lists the website, which is not
always the mail domain — ministries publish regjeringen.no but send mail from
<code>.dep.no; Miljødirektoratet's site is miljodirektoratet.no but mail is
miljodir.no). So the mail domain is curated below and documented per entry; the
scan itself then proves the platform from that domain's public DNS.

Run:  python3 gen_statlige_organ.py        # rewrites statlige_organ.json
Needs network (Enhetsregisteret, no auth, no cost). The produced JSON is COMMITTED
so the scan stays offline; re-run this only to refresh names/websites.
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "statlige_organ.json")
BRREG = "https://data.brreg.no/enhetsregisteret/api/enheter/{}"

# (display name, organisasjonsnummer, mail domain). The display name is editorial
# (the common short name); the legal name + website are fetched from the register.
# Mail domains are each confirmed to carry real public DNS mail signal. Ministries
# resolve to <code>.dep.no (the shared departementenes mail infrastructure, DSS).
CURATED = [
    # Departementer (ministries) — mail on the shared *.dep.no infrastructure (DSS)
    ("Statsministerens kontor",             "972417777", "smk.dep.no"),
    ("Finansdepartementet",                 "972417807", "fin.dep.no"),
    ("Justis- og beredskapsdepartementet",  "972417831", "jd.dep.no"),
    ("Utenriksdepartementet",               "972417920", "ud.dep.no"),
    ("Forsvarsdepartementet",               "972417823", "fd.dep.no"),
    ("Helse- og omsorgsdepartementet",      "983887406", "hod.dep.no"),
    ("Kommunal- og distriktsdepartementet", "972417858", "kdd.dep.no"),
    ("Kunnskapsdepartementet",              "872417842", "kd.dep.no"),
    ("Klima- og miljødepartementet",        "972417882", "kld.dep.no"),
    ("Nærings- og fiskeridepartementet",    "912660680", "nfd.dep.no"),
    ("Arbeids- og inkluderingsdepartementet", "983887457", "aid.dep.no"),
    ("Barne- og familiedepartementet",      "972417793", "bfd.dep.no"),
    ("Kultur- og likestillingsdepartementet", "972417866", "kud.dep.no"),
    ("Landbruks- og matdepartementet",      "972417874", "lmd.dep.no"),
    ("Samferdselsdepartementet",            "972417904", "sd.dep.no"),
    ("Energidepartementet",                 "977161630", "ed.dep.no"),
    # Direktorater og etater
    ("Skatteetaten",                        "974761076", "skatteetaten.no"),
    ("Tolletaten",                          "974761343", "toll.no"),
    ("NAV (Arbeids- og velferdsetaten)",    "889640782", "nav.no"),
    ("Statens lånekasse for utdanning",     "960885406", "lanekassen.no"),
    ("Husbanken",                           "942114184", "husbanken.no"),
    ("Helsedirektoratet",                   "983544622", "helsedirektoratet.no"),
    ("Folkehelseinstituttet",               "983744516", "fhi.no"),
    ("Statens helsetilsyn",                 "974761394", "helsetilsynet.no"),
    ("Utlendingsdirektoratet (UDI)",        "974760746", "udi.no"),
    ("Integrerings- og mangfoldsdirektoratet (IMDi)", "987879696", "imdi.no"),
    ("Barne-, ungdoms- og familiedirektoratet (Bufdir)", "986128433", "bufetat.no"),
    ("Digitaliseringsdirektoratet",         "991825827", "digdir.no"),
    ("Direktoratet for forvaltning og økonomistyring (DFØ)", "986252932", "dfo.no"),
    ("Statistisk sentralbyrå",              "971526920", "ssb.no"),
    ("Mattilsynet",                         "985399077", "mattilsynet.no"),
    ("Statens vegvesen",                    "971032081", "vegvesen.no"),
    ("Statens kartverk",                    "971040238", "kartverket.no"),
    ("Kystverket",                          "874783242", "kystverket.no"),
    ("Brønnøysundregistrene",               "974760673", "brreg.no"),
    ("Patentstyret",                        "971526157", "patentstyret.no"),
    ("Miljødirektoratet",                   "999601391", "miljodir.no"),
    ("Norges vassdrags- og energidirektorat (NVE)", "970205039", "nve.no"),
    ("Riksantikvaren",                      "974760819", "riksantikvaren.no"),
    ("Meteorologisk institutt",             "971274042", "met.no"),
    ("Arbeidstilsynet",                     "974761211", "arbeidstilsynet.no"),
    ("Direktoratet for samfunnssikkerhet og beredskap (DSB)", "974760983", "dsb.no"),
    ("Fiskeridirektoratet",                 "971203420", "fiskeridir.no"),
    ("Sjøfartsdirektoratet",                "974761262", "sdir.no"),
    ("Landbruksdirektoratet",               "981544315", "landbruksdirektoratet.no"),
    ("Utdanningsdirektoratet",              "970018131", "udir.no"),
    ("Direktoratet for høyere utdanning og kompetanse (HK-dir)", "974788985", "hkdir.no"),
    ("Norad",                               "971277882", "norad.no"),
    ("Jernbanedirektoratet",                "916810962", "jernbanedirektoratet.no"),
    ("Nasjonal kommunikasjonsmyndighet (Nkom)", "974446871", "nkom.no"),
    ("Statsbygg",                           "971278374", "statsbygg.no"),
    ("Statens pensjonskasse",               "982583462", "spk.no"),
    ("Datatilsynet",                        "974761467", "datatilsynet.no"),
    ("Konkurransetilsynet",                 "974761246", "konkurransetilsynet.no"),
    ("Finanstilsynet",                      "840747972", "finanstilsynet.no"),
    ("Luftfartstilsynet",                   "981105516", "luftfartstilsynet.no"),
    ("Medietilsynet",                       "974760886", "medietilsynet.no"),
    # Justis, politi og forsvar
    ("Politidirektoratet",                  "982531950", "politiet.no"),
    ("Forsvaret",                           "986105174", "mil.no"),
    ("Domstoladministrasjonen",             "926721720", "domstol.no"),
    ("Kriminalomsorgsdirektoratet",         "911830868", "kriminalomsorgen.no"),
    ("Riksrevisjonen",                      "974760843", "riksrevisjonen.no"),
]


def fetch_entity(orgnr):
    """Official name + website + org-form for one organisasjonsnummer (cited source)."""
    with urllib.request.urlopen(BRREG.format(orgnr), timeout=20) as r:
        d = json.load(r)
    return {
        "legal_name": d["navn"],
        "website": d.get("hjemmeside") or None,
        "orgform": d["organisasjonsform"]["kode"],
    }


def main():
    date = os.environ.get("SCAN_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    organ = []
    for name, orgnr, domain in CURATED:
        info = fetch_entity(orgnr)
        print(f"  {orgnr}  {info['legal_name']:46}  -> {domain}", file=sys.stderr)
        organ.append({
            "name": name,
            "legal_name": info["legal_name"],
            "orgnr": orgnr,
            "domain": domain,
            "website": info["website"],
            "orgform": info["orgform"],
            "source": BRREG.format(orgnr),
        })
    organ.sort(key=lambda o: o["name"])
    out = {
        "meta": {
            "title": "Norwegian state bodies (statlige organ) — email-scan seed",
            "source": "Brønnøysund Enhetsregisteret (legal name + website per orgnr)",
            "sourceUrl": "https://data.brreg.no/enhetsregisteret/api/enheter/",
            "sourceDate": date,
            "license": "CC BY 4.0",
            "selection": ("Curated selection of major central-state bodies "
                          "(departementer, direktorater, etater, tilsyn). Helseforetak "
                          "and universiteter/høgskoler are scanned as their own "
                          "categories (gen_sectors.py). This is a growing subset of the "
                          "fuller statlige sector, not the complete ~200. Identities "
                          "resolved from Enhetsregisteret; mail domains curated (the "
                          "register lists the website, not the mail domain) and "
                          "confirmed against public DNS."),
            "count": len(organ),
        },
        "organ": organ,
    }
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
    print(f"Wrote {OUT} ({len(organ)} statlige organ)", file=sys.stderr)


if __name__ == "__main__":
    main()
